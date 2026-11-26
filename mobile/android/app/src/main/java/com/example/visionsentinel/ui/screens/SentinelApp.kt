package com.example.visionsentinel.ui.screens

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.example.visionsentinel.ui.AppTab
import com.example.visionsentinel.ui.SentinelViewModel

@Composable
fun SentinelApp(
    viewModel: SentinelViewModel,
    onRequestNotificationPermission: () -> Unit,
) {
    val state by viewModel.state.collectAsState()

    val selectedAlertId = state.selectedAlertId
    if (selectedAlertId != null) {
        AlertDetailScreen(
            state = state,
            alertId = selectedAlertId,
            onBack = viewModel::closeAlertDetail,
            onAcknowledge = { viewModel.acknowledgeAlert(selectedAlertId) },
            onSubmitFeedback = { rating, note -> viewModel.submitFeedback(selectedAlertId, rating, note) },
            onLoadClip = { viewModel.loadClip(selectedAlertId) },
        )
        return
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = state.selectedTab == AppTab.ALERTS,
                    onClick = { viewModel.selectTab(AppTab.ALERTS) },
                    icon = {
                        BadgedBox(
                            badge = {
                                if (state.unackedCount > 0) {
                                    Badge { Text(state.unackedCount.toString()) }
                                }
                            },
                        ) {
                            Icon(Icons.Default.Notifications, contentDescription = "Alerts")
                        }
                    },
                    label = { Text("Alerts") },
                )
                NavigationBarItem(
                    selected = state.selectedTab == AppTab.SETTINGS,
                    onClick = { viewModel.selectTab(AppTab.SETTINGS) },
                    icon = { Icon(Icons.Default.Settings, contentDescription = "Settings") },
                    label = { Text("Settings") },
                )
            }
        },
    ) { innerPadding ->
        when (state.selectedTab) {
            AppTab.ALERTS -> AlertFeedScreen(
                state = state,
                modifier = Modifier.padding(innerPadding),
                onSelectFilter = viewModel::selectFilter,
                onRefresh = viewModel::refreshAlerts,
                onOpenAlert = viewModel::openAlert,
                onOpenSettings = { viewModel.selectTab(AppTab.SETTINGS) },
            )
            AppTab.SETTINGS -> SettingsScreen(
                state = state,
                modifier = Modifier.padding(innerPadding),
                onBaseUrlChange = viewModel::updateBaseUrl,
                onTokenChange = viewModel::updateHouseholdToken,
                onDeviceNameChange = viewModel::updateDeviceName,
                onSaveAndTest = viewModel::saveAndTestConnection,
                onRequestNotificationPermission = onRequestNotificationPermission,
                onRegisterFcm = viewModel::fetchFcmTokenAndRegister,
            )
        }
    }
}
