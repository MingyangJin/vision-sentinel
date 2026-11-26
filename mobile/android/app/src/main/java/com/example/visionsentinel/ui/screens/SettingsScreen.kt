package com.example.visionsentinel.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.example.visionsentinel.notifications.NotificationChannels
import com.example.visionsentinel.ui.SentinelUiState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    state: SentinelUiState,
    modifier: Modifier = Modifier,
    onBaseUrlChange: (String) -> Unit,
    onTokenChange: (String) -> Unit,
    onDeviceNameChange: (String) -> Unit,
    onSaveAndTest: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
    onRegisterFcm: () -> Unit,
) {
    val context = LocalContext.current

    Column(modifier = modifier.fillMaxSize()) {
        TopAppBar(title = { Text("Settings") })

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Notification Service", style = MaterialTheme.typography.titleMedium)

                    OutlinedTextField(
                        value = state.baseUrl,
                        onValueChange = onBaseUrlChange,
                        label = { Text("Server Base URL") },
                        placeholder = { Text("https://sentinel.example.com:8790") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    OutlinedTextField(
                        value = state.householdToken,
                        onValueChange = onTokenChange,
                        label = { Text("Shared Household Bearer Token") },
                        visualTransformation = PasswordVisualTransformation(),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    OutlinedTextField(
                        value = state.deviceName,
                        onValueChange = onDeviceNameChange,
                        label = { Text("Device Name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    Text(
                        "Stored locally via EncryptedSharedPreferences (Android Keystore).",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )

                    Button(
                        onClick = onSaveAndTest,
                        enabled = !state.isTestingConnection,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(if (state.isTestingConnection) "Testing Connection…" else "Save & Test Connection")
                    }

                    state.connectionMessage?.let { msg ->
                        Text(
                            text = msg,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (state.connectionOk) Color(0xFF4ADE80) else MaterialTheme.colorScheme.error,
                        )
                    }
                }
            }

            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Push Notifications (FCM + Wear OS)", style = MaterialTheme.typography.titleMedium)

                    Text(
                        "Notification Permission: ${if (state.notificationPermissionGranted) "Granted" else "Not granted"}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(
                        "Firebase Config: ${if (state.firebaseConfigured) "Active" else "Not bundled (add app/google-services.json)"}",
                        style = MaterialTheme.typography.bodySmall,
                    )

                    state.fcmToken?.let { token ->
                        val preview = if (token.length > 16) "${token.take(8)}…${token.takeLast(6)}" else token
                        Text(
                            "FCM Token: $preview",
                            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                        )
                    }

                    if (!state.notificationPermissionGranted) {
                        OutlinedButton(
                            onClick = onRequestNotificationPermission,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("Grant Notification Permission")
                        }
                    }

                    Button(
                        onClick = onRegisterFcm,
                        enabled = !state.isRegisteringDevice && state.isConfigured,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(if (state.isRegisteringDevice) "Registering…" else "Register FCM Device with Server")
                    }

                    state.registrationMessage?.let { msg ->
                        Text(
                            text = msg,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    Spacer(Modifier.height(4.dp))

                    OutlinedButton(
                        onClick = {
                            context.startActivity(NotificationChannels.urgentChannelSettingsIntent(context))
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Urgent Channel & DND Bypass Settings")
                    }

                    Text(
                        "Opens Android settings for the `sentinel_urgent` channel so you can allow urgent alerts to override Do Not Disturb.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
