package com.example.visionsentinel.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.visionsentinel.api.AlertRecord
import com.example.visionsentinel.api.AlertSeverity
import com.example.visionsentinel.api.FeedbackRating
import com.example.visionsentinel.ui.AlertFilter
import com.example.visionsentinel.ui.SentinelUiState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlertFeedScreen(
    state: SentinelUiState,
    modifier: Modifier = Modifier,
    onSelectFilter: (AlertFilter) -> Unit,
    onRefresh: () -> Unit,
    onOpenAlert: (String) -> Unit,
    onOpenSettings: () -> Unit,
) {
    Column(modifier = modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("Vision Sentinel") },
            actions = {
                IconButton(
                    onClick = onRefresh,
                    enabled = state.isConfigured && !state.isLoadingAlerts,
                ) {
                    if (state.isLoadingAlerts) {
                        CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.padding(8.dp))
                    } else {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh alerts")
                    }
                }
            },
        )

        if (!state.isConfigured) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        "Server Not Configured",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Enter your Notification Service URL and shared household bearer token in Settings.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(16.dp))
                    Button(onClick = onOpenSettings) {
                        Text("Open Settings")
                    }
                }
            }
            return
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            AlertFilter.entries.forEach { item ->
                FilterChip(
                    selected = state.filter == item,
                    onClick = { onSelectFilter(item) },
                    label = { Text(item.label) },
                )
            }
        }

        state.alertsError?.let { err ->
            Surface(
                color = MaterialTheme.colorScheme.errorContainer,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                shape = MaterialTheme.shapes.small,
            ) {
                Text(
                    text = err,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(12.dp),
                )
            }
        }

        val alerts = state.filteredAlerts
        if (alerts.isEmpty() && !state.isLoadingAlerts) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "No alerts match this filter.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(alerts, key = { it.id }) { alert ->
                    AlertCard(alert = alert, onClick = { onOpenAlert(alert.id) })
                }
            }
        }
    }
}

@Composable
private fun AlertCard(alert: AlertRecord, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SeverityPill(alert.severity)
                    Text(
                        text = alert.topicId,
                        style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    text = alert.createdAt.take(19).replace("T", " "),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Text(
                text = alert.title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )

            if (alert.body.isNotBlank()) {
                Text(
                    text = alert.body,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (alert.isAcknowledged) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Icon(
                            Icons.Default.CheckCircle,
                            contentDescription = null,
                            tint = Color(0xFF22C55E),
                        )
                        Text("Acked", style = MaterialTheme.typography.labelSmall, color = Color(0xFF22C55E))
                    }
                } else {
                    Text(
                        "Unacknowledged",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFFF59E0B),
                    )
                }

                alert.latestFeedback?.let { fb ->
                    val isUp = fb.rating == FeedbackRating.UP
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Icon(
                            if (isUp) Icons.Default.ThumbUp else Icons.Default.ThumbDown,
                            contentDescription = null,
                            tint = if (isUp) Color(0xFF60A5FA) else Color(0xFFF87171),
                        )
                        Text(
                            fb.rating.displayName(),
                            style = MaterialTheme.typography.labelSmall,
                            color = if (isUp) Color(0xFF60A5FA) else Color(0xFFF87171),
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun SeverityPill(severity: AlertSeverity) {
    val bg = when (severity) {
        AlertSeverity.INFO -> Color(0xFF2563EB)
        AlertSeverity.WARNING -> Color(0xFFD97706)
        AlertSeverity.URGENT -> Color(0xFFDC2626)
    }
    Surface(
        color = bg,
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            text = severity.displayName().uppercase(),
            color = Color.White,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}
