package com.example.visionsentinel

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import com.example.visionsentinel.notifications.SentinelMessagingService
import com.example.visionsentinel.ui.SentinelViewModel
import com.example.visionsentinel.ui.screens.SentinelApp
import com.example.visionsentinel.ui.theme.VisionSentinelTheme

class MainActivity : ComponentActivity() {
    private val viewModel: SentinelViewModel by viewModels()

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        viewModel.onNotificationPermissionResult(granted)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleDeepLink(intent)
        refreshNotificationPermissionState()

        setContent {
            VisionSentinelTheme {
                SentinelApp(
                    viewModel = viewModel,
                    onRequestNotificationPermission = { requestNotificationPermissionIfNeeded() },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleDeepLink(intent)
    }

    private fun handleDeepLink(intent: Intent?) {
        val alertId = intent?.getStringExtra(SentinelMessagingService.EXTRA_ALERT_ID)
        if (!alertId.isNullOrBlank()) {
            viewModel.openAlert(alertId)
        }
    }

    private fun refreshNotificationPermissionState() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            viewModel.onNotificationPermissionResult(granted)
        } else {
            viewModel.onNotificationPermissionResult(true)
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            viewModel.onNotificationPermissionResult(true)
        }
    }
}
