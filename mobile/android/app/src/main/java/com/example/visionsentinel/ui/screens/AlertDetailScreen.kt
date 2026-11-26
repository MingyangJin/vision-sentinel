package com.example.visionsentinel.ui.screens

import android.net.Uri
import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.example.visionsentinel.api.FeedbackRating
import com.example.visionsentinel.ui.SentinelUiState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlertDetailScreen(
    state: SentinelUiState,
    alertId: String,
    onBack: () -> Unit,
    onAcknowledge: () -> Unit,
    onSubmitFeedback: (FeedbackRating, String) -> Unit,
    onLoadClip: () -> Unit,
) {
    var pendingFeedbackRating by remember { mutableStateOf<FeedbackRating?>(null) }
    val alert = state.selectedAlert

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Alert Detail") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { innerPadding ->
        if (alert == null) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SeverityPill(alert.severity)
                Text(
                    text = alert.topicId,
                    style = MaterialTheme.typography.labelLarge.copy(fontFamily = FontFamily.Monospace),
                )
            }

            Text(
                text = alert.title,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
            )

            if (alert.body.isNotBlank()) {
                Text(
                    text = alert.body,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Text(
                text = "Created: ${alert.createdAt}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            // Authenticated media preview (thumbnail + clip)
            if (alert.thumbnailAvailable || alert.clipAvailable) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(
                        modifier = Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Text("Observation Media", style = MaterialTheme.typography.titleSmall)

                        val clipFile = state.detailClipFile
                        if (clipFile != null) {
                            AndroidView(
                                factory = { ctx ->
                                    VideoView(ctx).apply {
                                        val controller = MediaController(ctx)
                                        controller.setAnchorView(this)
                                        setMediaController(controller)
                                        setVideoURI(Uri.fromFile(clipFile))
                                        start()
                                    }
                                },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(220.dp),
                            )
                        } else if (state.detailThumbnail != null) {
                            Image(
                                bitmap = state.detailThumbnail.asImageBitmap(),
                                contentDescription = "Alert thumbnail",
                                contentScale = ContentScale.Fit,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(220.dp),
                            )
                        } else if (state.isLoadingThumbnail) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(160.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                CircularProgressIndicator()
                            }
                        }

                        if (alert.clipAvailable && clipFile == null) {
                            OutlinedButton(
                                onClick = onLoadClip,
                                enabled = !state.isLoadingClip,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Icon(Icons.Default.PlayArrow, contentDescription = null)
                                Spacer(Modifier.padding(horizontal = 4.dp))
                                Text(if (state.isLoadingClip) "Downloading Clip…" else "Play Authenticated Clip")
                            }
                        }

                        state.mediaError?.let { err ->
                            Text(
                                text = err,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            // Acknowledge section
            if (alert.isAcknowledged) {
                Surface(
                    color = Color(0xFF14532D).copy(alpha = 0.35f),
                    shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Icon(Icons.Default.CheckCircle, contentDescription = null, tint = Color(0xFF4ADE80))
                        Column {
                            Text("Acknowledged", fontWeight = FontWeight.SemiBold)
                            Text(
                                "By ${alert.ackedBy ?: "device"} · ${alert.ackedAt ?: ""}",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            } else {
                Button(
                    onClick = onAcknowledge,
                    enabled = !state.actionInFlight,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Default.CheckCircle, contentDescription = null)
                    Spacer(Modifier.padding(horizontal = 4.dp))
                    Text(if (state.actionInFlight) "Working…" else "Acknowledge Alert")
                }
            }

            // Feedback section (separate from ack)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Was this alert helpful?", style = MaterialTheme.typography.titleSmall)
                Text(
                    "Feedback is logged separately from acknowledgment and pins the observation bundle for review.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    OutlinedButton(
                        onClick = { pendingFeedbackRating = FeedbackRating.UP },
                        enabled = !state.actionInFlight,
                        modifier = Modifier.weight(1f),
                    ) {
                        Icon(Icons.Default.ThumbUp, contentDescription = null)
                        Spacer(Modifier.padding(horizontal = 4.dp))
                        Text("Thumbs Up")
                    }
                    OutlinedButton(
                        onClick = { pendingFeedbackRating = FeedbackRating.DOWN },
                        enabled = !state.actionInFlight,
                        modifier = Modifier.weight(1f),
                    ) {
                        Icon(Icons.Default.ThumbDown, contentDescription = null)
                        Spacer(Modifier.padding(horizontal = 4.dp))
                        Text("Thumbs Down")
                    }
                }
            }

            state.actionStatusMessage?.let { msg ->
                Text(msg, color = Color(0xFF4ADE80), style = MaterialTheme.typography.bodySmall)
            }
            state.actionError?.let { err ->
                Text(err, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }

            if (alert.feedback.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Feedback History", style = MaterialTheme.typography.titleSmall)
                    alert.feedback.forEach { fb ->
                        Card(
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(fb.rating.displayName(), fontWeight = FontWeight.SemiBold)
                                if (fb.note.isNotBlank()) {
                                    Text(fb.note, style = MaterialTheme.typography.bodyMedium)
                                }
                                Text(
                                    "${fb.by.ifBlank { "device" }} · ${fb.at}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }

            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Metadata", style = MaterialTheme.typography.titleSmall)
                    Text("Alert ID: $alertId", style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace))
                    if (alert.request.cameraIds.isNotEmpty()) {
                        Text("Cameras: ${alert.request.cameraIds.joinToString(", ")}", style = MaterialTheme.typography.bodySmall)
                    }
                    alert.request.bundleId?.let { bundle ->
                        Text("Bundle: $bundle", style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace))
                    }
                    Text("Source: ${alert.request.source}", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }

    pendingFeedbackRating?.let { initialRating ->
        FeedbackDialog(
            initialRating = initialRating,
            onDismiss = { pendingFeedbackRating = null },
            onSubmit = { rating, note ->
                pendingFeedbackRating = null
                onSubmitFeedback(rating, note)
            },
        )
    }
}

@Composable
private fun FeedbackDialog(
    initialRating: FeedbackRating,
    onDismiss: () -> Unit,
    onSubmit: (FeedbackRating, String) -> Unit,
) {
    var rating by remember { mutableStateOf(initialRating) }
    var note by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Rate Alert") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (rating == FeedbackRating.UP) {
                        Button(onClick = { rating = FeedbackRating.UP }, modifier = Modifier.weight(1f)) {
                            Text("True Alarm")
                        }
                        OutlinedButton(onClick = { rating = FeedbackRating.DOWN }, modifier = Modifier.weight(1f)) {
                            Text("False Alarm")
                        }
                    } else {
                        OutlinedButton(onClick = { rating = FeedbackRating.UP }, modifier = Modifier.weight(1f)) {
                            Text("True Alarm")
                        }
                        Button(onClick = { rating = FeedbackRating.DOWN }, modifier = Modifier.weight(1f)) {
                            Text("False Alarm")
                        }
                    }
                }

                OutlinedTextField(
                    value = note,
                    onValueChange = { note = it },
                    label = { Text("Optional note") },
                    placeholder = { Text("e.g., Ignore tree shadow on left") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(onClick = { onSubmit(rating, note) }) {
                Text("Submit")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel")
            }
        },
    )
}
