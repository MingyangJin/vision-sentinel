package com.example.visionsentinel.api

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Stores server configuration and the shared household bearer token using
 * `EncryptedSharedPreferences` backed by the Android Keystore (`AES256_GCM`).
 */
class SettingsStore(context: Context) {
    private val appContext = context.applicationContext

    private val prefs: SharedPreferences by lazy {
        runCatching {
            val masterKey = MasterKey.Builder(appContext)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                appContext,
                PREFS_NAME,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        }.getOrElse {
            // Fallback only if Keystore is unavailable in an emulator/test environment.
            appContext.getSharedPreferences(PREFS_NAME_FALLBACK, Context.MODE_PRIVATE)
        }
    }

    fun loadConfig(): SentinelConfig {
        val defaultName = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android Device"
        return SentinelConfig(
            baseUrl = prefs.getString(KEY_BASE_URL, "") ?: "",
            householdToken = prefs.getString(KEY_BEARER_TOKEN, "") ?: "",
            deviceName = prefs.getString(KEY_DEVICE_NAME, defaultName) ?: defaultName,
        )
    }

    fun saveConfig(config: SentinelConfig) {
        prefs.edit()
            .putString(KEY_BASE_URL, config.baseUrl.trim())
            .putString(KEY_BEARER_TOKEN, config.householdToken.trim())
            .putString(KEY_DEVICE_NAME, config.deviceName.trim().ifEmpty { "Android Device" })
            .apply()
    }

    fun loadFcmToken(): String? = prefs.getString(KEY_FCM_TOKEN, null)?.takeIf { it.isNotBlank() }

    fun saveFcmToken(token: String) {
        prefs.edit().putString(KEY_FCM_TOKEN, token.trim()).apply()
    }

    companion object {
        private const val PREFS_NAME = "vision_sentinel_secure_prefs"
        private const val PREFS_NAME_FALLBACK = "vision_sentinel_prefs_fallback"
        private const val KEY_BASE_URL = "server_base_url"
        private const val KEY_BEARER_TOKEN = "household_bearer_token"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_FCM_TOKEN = "fcm_token"
    }
}
