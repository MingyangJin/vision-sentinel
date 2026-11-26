package com.example.visionsentinel.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import com.example.visionsentinel.api.FeedbackRating
import com.example.visionsentinel.api.SentinelApiClient
import com.example.visionsentinel.api.SettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Handles inline notification actions tapped on the Android shade or a paired Wear OS watch:
 * - `ACTION_ACK` -> `POST /v1/alerts/{id}/ack`
 * - `ACTION_FEEDBACK_UP` -> `POST /v1/alerts/{id}/feedback` (`rating = "up"`)
 * - `ACTION_FEEDBACK_DOWN` -> `POST /v1/alerts/{id}/feedback` (`rating = "down"`)
 */
class AlertActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val alertId = intent.getStringExtra(EXTRA_ALERT_ID)?.takeIf { it.isNotBlank() } ?: return
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, alertId.hashCode())
        val action = intent.action ?: return

        val pendingResult = goAsync()
        val appContext = context.applicationContext
        val store = SettingsStore(appContext)
        val config = store.loadConfig()

        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                if (!config.isConfigured) return@launch
                val client = SentinelApiClient(config)
                when (action) {
                    ACTION_ACK -> {
                        client.acknowledgeAlert(alertId = alertId, by = config.deviceName)
                        NotificationManagerCompat.from(appContext).cancel(notificationId)
                    }
                    ACTION_FEEDBACK_UP -> {
                        client.submitFeedback(
                            alertId = alertId,
                            rating = FeedbackRating.UP,
                            note = "",
                            by = config.deviceName,
                        )
                        NotificationManagerCompat.from(appContext).cancel(notificationId)
                    }
                    ACTION_FEEDBACK_DOWN -> {
                        client.submitFeedback(
                            alertId = alertId,
                            rating = FeedbackRating.DOWN,
                            note = "",
                            by = config.deviceName,
                        )
                        NotificationManagerCompat.from(appContext).cancel(notificationId)
                    }
                }
            } catch (_: Exception) {
                // Keep notification visible so the user can retry from the shade or open the app.
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        const val ACTION_ACK = "com.example.visionsentinel.action.ACK"
        const val ACTION_FEEDBACK_UP = "com.example.visionsentinel.action.FEEDBACK_UP"
        const val ACTION_FEEDBACK_DOWN = "com.example.visionsentinel.action.FEEDBACK_DOWN"
        const val EXTRA_ALERT_ID = "vision_sentinel.extra.ALERT_ID"
        const val EXTRA_NOTIFICATION_ID = "vision_sentinel.extra.NOTIFICATION_ID"
    }
}
