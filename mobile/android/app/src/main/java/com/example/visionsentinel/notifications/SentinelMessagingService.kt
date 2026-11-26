package com.example.visionsentinel.notifications

import android.Manifest
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.example.visionsentinel.MainActivity
import com.example.visionsentinel.api.AlertSeverity
import com.example.visionsentinel.api.FcmAlertPayload
import com.example.visionsentinel.api.SentinelApiClient
import com.example.visionsentinel.api.SettingsStore
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Receives FCM HTTP v1 pushes from Vision Sentinel's `FcmSink`.
 *
 * - Routes urgent pushes to `sentinel_urgent` (alarm audio + DND override) and standard pushes
 *   to `sentinel_default`.
 * - Attaches inline Acknowledge, Thumbs Up, and Thumbs Down actions on both the phone
 *   notification and paired Wear OS watches via `NotificationCompat.WearableExtender`.
 */
class SentinelMessagingService : FirebaseMessagingService() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        val store = SettingsStore(applicationContext)
        store.saveFcmToken(token)
        val config = store.loadConfig()
        if (!config.isConfigured) return

        serviceScope.launch {
            runCatching {
                val client = SentinelApiClient(config)
                client.registerDevice(pushToken = token, deviceName = config.deviceName, platform = "android")
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        NotificationChannels.ensureCreated(applicationContext)

        val payload = FcmAlertPayload.fromData(
            data = message.data,
            fallbackTitle = message.notification?.title,
            fallbackBody = message.notification?.body,
        ) ?: return

        showAlertNotification(payload)
    }

    private fun showAlertNotification(payload: FcmAlertPayload) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) return
        }

        val notificationId = payload.alertId.hashCode()
        val channelId = if (payload.channelId == NotificationChannels.CHANNEL_URGENT ||
            payload.severity == AlertSeverity.URGENT
        ) {
            NotificationChannels.CHANNEL_URGENT
        } else {
            NotificationChannels.CHANNEL_DEFAULT
        }

        val contentIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_ALERT_ID, payload.alertId)
        }
        val openPendingIntent = PendingIntent.getActivity(
            this,
            notificationId,
            contentIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val ackAction = buildInlineAction(
            alertId = payload.alertId,
            notificationId = notificationId,
            action = AlertActionReceiver.ACTION_ACK,
            label = "Acknowledge",
            iconRes = android.R.drawable.ic_menu_view,
            requestCodeOffset = 1,
            semanticAction = NotificationCompat.Action.SEMANTIC_ACTION_MARK_AS_READ,
        )
        val thumbsUpAction = buildInlineAction(
            alertId = payload.alertId,
            notificationId = notificationId,
            action = AlertActionReceiver.ACTION_FEEDBACK_UP,
            label = "Thumbs Up",
            iconRes = android.R.drawable.btn_star_big_on,
            requestCodeOffset = 2,
            semanticAction = NotificationCompat.Action.SEMANTIC_ACTION_THUMBS_UP,
        )
        val thumbsDownAction = buildInlineAction(
            alertId = payload.alertId,
            notificationId = notificationId,
            action = AlertActionReceiver.ACTION_FEEDBACK_DOWN,
            label = "Thumbs Down",
            iconRes = android.R.drawable.ic_delete,
            requestCodeOffset = 3,
            semanticAction = NotificationCompat.Action.SEMANTIC_ACTION_THUMBS_DOWN,
        )

        val wearableExtender = NotificationCompat.WearableExtender()
            .addAction(ackAction)
            .addAction(thumbsUpAction)
            .addAction(thumbsDownAction)

        val category = if (payload.severity == AlertSeverity.URGENT) {
            NotificationCompat.CATEGORY_ALARM
        } else {
            NotificationCompat.CATEGORY_EVENT
        }

        val builder = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(payload.title)
            .setSubText(payload.topicId)
            .setContentText(payload.body.ifBlank { "Topic: ${payload.topicId}" })
            .setStyle(NotificationCompat.BigTextStyle().bigText(payload.body.ifBlank { payload.title }))
            .setCategory(category)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setAutoCancel(true)
            .setContentIntent(openPendingIntent)
            .addAction(ackAction)
            .addAction(thumbsUpAction)
            .addAction(thumbsDownAction)
            .extend(wearableExtender)

        NotificationManagerCompat.from(this).notify(notificationId, builder.build())
    }

    private fun buildInlineAction(
        alertId: String,
        notificationId: Int,
        action: String,
        label: String,
        iconRes: Int,
        requestCodeOffset: Int,
        semanticAction: Int,
    ): NotificationCompat.Action {
        val intent = Intent(this, AlertActionReceiver::class.java).apply {
            this.action = action
            putExtra(AlertActionReceiver.EXTRA_ALERT_ID, alertId)
            putExtra(AlertActionReceiver.EXTRA_NOTIFICATION_ID, notificationId)
        }
        val pending = PendingIntent.getBroadcast(
            this,
            notificationId * 10 + requestCodeOffset,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Action.Builder(iconRes, label, pending)
            .setShowsUserInterface(false)
            .setSemanticAction(semanticAction)
            .build()
    }

    companion object {
        const val EXTRA_ALERT_ID = "vision_sentinel.extra.ALERT_ID"
    }
}
