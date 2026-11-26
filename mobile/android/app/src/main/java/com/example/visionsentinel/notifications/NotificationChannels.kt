package com.example.visionsentinel.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.provider.Settings
import com.example.visionsentinel.api.FcmAlertPayload

/**
 * Registers the two Android notification channels expected by Vision Sentinel's `FcmSink`:
 * - `sentinel_default`: standard alerts (info / warning).
 * - `sentinel_urgent`: urgent alerts configured with alarm audio attributes, strong vibration,
 *   and DND bypass request (users can confirm/toggle DND override via the Settings shortcut).
 */
object NotificationChannels {
    const val CHANNEL_DEFAULT = FcmAlertPayload.CHANNEL_DEFAULT
    const val CHANNEL_URGENT = FcmAlertPayload.CHANNEL_URGENT

    fun ensureCreated(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return

        val standardChannel = NotificationChannel(
            CHANNEL_DEFAULT,
            "Vision Sentinel Alerts",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Standard info and warning alerts from Vision Sentinel Topics."
            enableVibration(true)
        }

        val alarmSound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val alarmAudio = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()

        val urgentChannel = NotificationChannel(
            CHANNEL_URGENT,
            "Urgent Vision Alerts",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "High-priority urgent alerts with alarm sound and optional Do Not Disturb override."
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 450, 200, 450, 200, 700)
            setSound(alarmSound, alarmAudio)
            setBypassDnd(true)
            lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
        }

        manager.createNotificationChannel(standardChannel)
        manager.createNotificationChannel(urgentChannel)
    }

    /** Opens Android's system channel settings for `sentinel_urgent` so the user can enable Override DND. */
    fun urgentChannelSettingsIntent(context: Context): Intent =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).apply {
                putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                putExtra(Settings.EXTRA_CHANNEL_ID, CHANNEL_URGENT)
            }
        } else {
            Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            }
        }
}
