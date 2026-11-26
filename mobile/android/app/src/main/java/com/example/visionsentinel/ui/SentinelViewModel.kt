package com.example.visionsentinel.ui

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.visionsentinel.api.AlertRecord
import com.example.visionsentinel.api.AlertSeverity
import com.example.visionsentinel.api.FeedbackRating
import com.example.visionsentinel.api.SentinelApiClient
import com.example.visionsentinel.api.SentinelConfig
import com.example.visionsentinel.api.SettingsStore
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

enum class AlertFilter(val label: String) {
    ALL("All"),
    UNACKED("Unacked"),
    URGENT("Urgent"),
}

enum class AppTab {
    ALERTS,
    SETTINGS,
}

data class SentinelUiState(
    val baseUrl: String = "",
    val householdToken: String = "",
    val deviceName: String = "Android Device",
    val fcmToken: String? = null,
    val notificationPermissionGranted: Boolean = false,
    val firebaseConfigured: Boolean = false,
    val selectedTab: AppTab = AppTab.ALERTS,
    val selectedAlertId: String? = null,
    val filter: AlertFilter = AlertFilter.ALL,
    val alerts: List<AlertRecord> = emptyList(),
    val isLoadingAlerts: Boolean = false,
    val alertsError: String? = null,
    val isTestingConnection: Boolean = false,
    val connectionMessage: String? = null,
    val connectionOk: Boolean = false,
    val registrationMessage: String? = null,
    val isRegisteringDevice: Boolean = false,
    val detailThumbnail: Bitmap? = null,
    val isLoadingThumbnail: Boolean = false,
    val detailClipFile: File? = null,
    val isLoadingClip: Boolean = false,
    val mediaError: String? = null,
    val actionInFlight: Boolean = false,
    val actionStatusMessage: String? = null,
    val actionError: String? = null,
) {
    val config: SentinelConfig
        get() = SentinelConfig(baseUrl = baseUrl, householdToken = householdToken, deviceName = deviceName)

    val isConfigured: Boolean
        get() = config.isConfigured

    val filteredAlerts: List<AlertRecord>
        get() = when (filter) {
            AlertFilter.ALL -> alerts
            AlertFilter.UNACKED -> alerts.filter { !it.isAcknowledged }
            AlertFilter.URGENT -> alerts.filter { it.severity == AlertSeverity.URGENT }
        }

    val unackedCount: Int
        get() = alerts.count { !it.isAcknowledged }

    val selectedAlert: AlertRecord?
        get() = selectedAlertId?.let { id -> alerts.firstOrNull { it.id == id } }
}

class SentinelViewModel(application: Application) : AndroidViewModel(application) {
    private val store = SettingsStore(application)
    private val _state = MutableStateFlow(initialState())
    val state: StateFlow<SentinelUiState> = _state.asStateFlow()

    init {
        if (_state.value.isConfigured) {
            refreshAlerts()
        } else {
            _state.update { it.copy(selectedTab = AppTab.SETTINGS) }
        }
    }

    private fun initialState(): SentinelUiState {
        val cfg = store.loadConfig()
        val firebaseReady = runCatching {
            FirebaseApp.getApps(getApplication()).isNotEmpty()
        }.getOrDefault(false)
        return SentinelUiState(
            baseUrl = cfg.baseUrl,
            householdToken = cfg.householdToken,
            deviceName = cfg.deviceName,
            fcmToken = store.loadFcmToken(),
            firebaseConfigured = firebaseReady,
        )
    }

    fun onNotificationPermissionResult(granted: Boolean) {
        _state.update { it.copy(notificationPermissionGranted = granted) }
    }

    fun selectTab(tab: AppTab) {
        _state.update { it.copy(selectedTab = tab, selectedAlertId = null) }
    }

    fun selectFilter(filter: AlertFilter) {
        _state.update { it.copy(filter = filter) }
    }

    fun updateBaseUrl(value: String) {
        _state.update { it.copy(baseUrl = value) }
    }

    fun updateHouseholdToken(value: String) {
        _state.update { it.copy(householdToken = value) }
    }

    fun updateDeviceName(value: String) {
        _state.update { it.copy(deviceName = value) }
    }

    fun saveAndTestConnection() {
        val cfg = _state.value.config
        store.saveConfig(cfg)
        viewModelScope.launch {
            _state.update { it.copy(isTestingConnection = true, connectionMessage = null) }
            try {
                val client = SentinelApiClient(cfg)
                val result = client.testConnection()
                val sinkLabel = if (result.sinks.isEmpty()) "none" else result.sinks.joinToString(", ")
                _state.update {
                    it.copy(
                        isTestingConnection = false,
                        connectionOk = true,
                        connectionMessage = "Connected. Active sinks: $sinkLabel.",
                    )
                }
                refreshAlerts()
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        isTestingConnection = false,
                        connectionOk = false,
                        connectionMessage = e.message ?: "Connection failed.",
                    )
                }
            }
        }
    }

    fun fetchFcmTokenAndRegister() {
        val cfg = _state.value.config
        store.saveConfig(cfg)
        if (!cfg.isConfigured) {
            _state.update { it.copy(registrationMessage = "Save a valid Server URL and Bearer Token first.") }
            return
        }

        val firebaseReady = runCatching {
            FirebaseApp.getApps(getApplication()).isNotEmpty()
        }.getOrDefault(false)

        if (!firebaseReady) {
            _state.update {
                it.copy(
                    firebaseConfigured = false,
                    registrationMessage = "Firebase is not configured yet. Add app/google-services.json and rebuild (see mobile/android/README.md).",
                )
            }
            return
        }

        _state.update { it.copy(isRegisteringDevice = true, registrationMessage = null, firebaseConfigured = true) }
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                store.saveFcmToken(token)
                _state.update { it.copy(fcmToken = token) }
                registerFcmTokenWithServer(token)
            }
            .addOnFailureListener { err ->
                _state.update {
                    it.copy(
                        isRegisteringDevice = false,
                        registrationMessage = "Failed to fetch FCM token: ${err.message}",
                    )
                }
            }
    }

    private fun registerFcmTokenWithServer(token: String) {
        val cfg = _state.value.config
        viewModelScope.launch {
            try {
                val client = SentinelApiClient(cfg)
                val resp = client.registerDevice(pushToken = token, deviceName = cfg.deviceName, platform = "android")
                _state.update {
                    it.copy(
                        isRegisteringDevice = false,
                        registrationMessage = "Registered \"${resp.name}\" (${resp.platform}) with server.",
                    )
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        isRegisteringDevice = false,
                        registrationMessage = "Device registration failed: ${e.message}",
                    )
                }
            }
        }
    }

    fun refreshAlerts() {
        val cfg = _state.value.config
        if (!cfg.isConfigured) {
            _state.update { it.copy(alerts = emptyList(), alertsError = null) }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(isLoadingAlerts = true, alertsError = null) }
            try {
                val client = SentinelApiClient(cfg)
                val items = client.listAlerts(limit = 100)
                _state.update { it.copy(alerts = items, isLoadingAlerts = false) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        isLoadingAlerts = false,
                        alertsError = e.message ?: "Failed to load alerts.",
                    )
                }
            }
        }
    }

    fun openAlert(alertId: String) {
        _state.update {
            it.copy(
                selectedTab = AppTab.ALERTS,
                selectedAlertId = alertId,
                detailThumbnail = null,
                detailClipFile = null,
                mediaError = null,
                actionStatusMessage = null,
                actionError = null,
            )
        }
        loadAlertDetailAndThumbnail(alertId)
    }

    fun closeAlertDetail() {
        _state.update {
            it.copy(
                selectedAlertId = null,
                detailThumbnail = null,
                detailClipFile = null,
                mediaError = null,
                actionStatusMessage = null,
                actionError = null,
            )
        }
    }

    private fun loadAlertDetailAndThumbnail(alertId: String) {
        val cfg = _state.value.config
        if (!cfg.isConfigured) return
        viewModelScope.launch {
            try {
                val client = SentinelApiClient(cfg)
                var current = _state.value.alerts.firstOrNull { it.id == alertId }
                if (current == null) {
                    current = client.getAlert(alertId)
                    upsertAlert(current)
                }
                if (current.thumbnailAvailable) {
                    _state.update { it.copy(isLoadingThumbnail = true, mediaError = null) }
                    try {
                        val bytes = client.fetchThumbnail(alertId)
                        val bitmap = withContext(Dispatchers.Default) {
                            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        }
                        _state.update { it.copy(detailThumbnail = bitmap, isLoadingThumbnail = false) }
                    } catch (e: Exception) {
                        _state.update {
                            it.copy(
                                isLoadingThumbnail = false,
                                mediaError = "Thumbnail unavailable: ${e.message}",
                            )
                        }
                    }
                }
            } catch (e: Exception) {
                _state.update { it.copy(actionError = e.message) }
            }
        }
    }

    fun loadClip(alertId: String) {
        val cfg = _state.value.config
        if (!cfg.isConfigured) return
        viewModelScope.launch {
            _state.update { it.copy(isLoadingClip = true, mediaError = null) }
            try {
                val client = SentinelApiClient(cfg)
                val bytes = client.fetchClip(alertId)
                val file = withContext(Dispatchers.IO) {
                    val safeName = alertId.replace(Regex("[^A-Za-z0-9._-]"), "_")
                    File(getApplication<Application>().cacheDir, "vs-clip-$safeName.mp4").apply {
                        writeBytes(bytes)
                    }
                }
                _state.update { it.copy(detailClipFile = file, isLoadingClip = false) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        isLoadingClip = false,
                        mediaError = "Could not load clip: ${e.message}",
                    )
                }
            }
        }
    }

    fun acknowledgeAlert(alertId: String) {
        val cfg = _state.value.config
        if (!cfg.isConfigured) return
        viewModelScope.launch {
            _state.update { it.copy(actionInFlight = true, actionError = null, actionStatusMessage = null) }
            try {
                val client = SentinelApiClient(cfg)
                val updated = client.acknowledgeAlert(alertId = alertId, by = cfg.deviceName)
                upsertAlert(updated)
                _state.update {
                    it.copy(actionInFlight = false, actionStatusMessage = "Alert acknowledged.")
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(actionInFlight = false, actionError = e.message ?: "Acknowledge failed.")
                }
            }
        }
    }

    fun submitFeedback(alertId: String, rating: FeedbackRating, note: String) {
        val cfg = _state.value.config
        if (!cfg.isConfigured) return
        viewModelScope.launch {
            _state.update { it.copy(actionInFlight = true, actionError = null, actionStatusMessage = null) }
            try {
                val client = SentinelApiClient(cfg)
                val response = client.submitFeedback(
                    alertId = alertId,
                    rating = rating,
                    note = note.trim(),
                    by = cfg.deviceName,
                )
                upsertAlert(response.alert)
                val msg = if (response.pinned) {
                    "Feedback saved and observation bundle pinned."
                } else {
                    "Feedback saved."
                }
                _state.update { it.copy(actionInFlight = false, actionStatusMessage = msg) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(actionInFlight = false, actionError = e.message ?: "Feedback failed.")
                }
            }
        }
    }

    private fun upsertAlert(updated: AlertRecord) {
        _state.update { current ->
            val list = current.alerts.toMutableList()
            val idx = list.indexOfFirst { it.id == updated.id }
            if (idx >= 0) {
                list[idx] = updated
            } else {
                list.add(0, updated)
            }
            current.copy(alerts = list)
        }
    }
}
