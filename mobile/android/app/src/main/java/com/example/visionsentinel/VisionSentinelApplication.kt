package com.example.visionsentinel

import android.app.Application
import com.example.visionsentinel.notifications.NotificationChannels

class VisionSentinelApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        NotificationChannels.ensureCreated(this)
    }
}
