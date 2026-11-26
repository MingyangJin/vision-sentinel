package com.example.visionsentinel.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class AlertSeverity {
    @SerialName("info")
    INFO,

    @SerialName("warning")
    WARNING,

    @SerialName("urgent")
    URGENT;

    fun wireValue(): String = name.lowercase()

    fun displayName(): String = when (this) {
        INFO -> "Info"
        WARNING -> "Warning"
        URGENT -> "Urgent"
    }

    companion object {
        fun fromWire(value: String?): AlertSeverity = when (value?.lowercase()) {
            "urgent" -> URGENT
            "warning" -> WARNING
            else -> INFO
        }
    }
}

@Serializable
enum class FeedbackRating {
    @SerialName("up")
    UP,

    @SerialName("down")
    DOWN;

    fun wireValue(): String = name.lowercase()

    fun displayName(): String = when (this) {
        UP -> "True Alarm"
        DOWN -> "False Alarm"
    }
}

@Serializable
data class AlertRequestPayload(
    @SerialName("topic_id") val topicId: String,
    val severity: AlertSeverity = AlertSeverity.INFO,
    val title: String,
    val body: String = "",
    @SerialName("clip_ref") val clipRef: String? = null,
    @SerialName("dedupe_key") val dedupeKey: String? = null,
    @SerialName("cooldown_sec") val cooldownSec: Double? = null,
    @SerialName("requires_ack") val requiresAck: Boolean = false,
    @SerialName("bundle_id") val bundleId: String? = null,
    @SerialName("camera_ids") val cameraIds: List<String> = emptyList(),
    val source: String = "main_agent",
    @SerialName("rubric_version") val rubricVersion: String? = null,
    @SerialName("tool_versions") val toolVersions: Map<String, String> = emptyMap(),
    val speaker: Boolean = false,
)

@Serializable
data class FeedbackItem(
    @SerialName("alert_id") val alertId: String,
    val rating: FeedbackRating,
    val note: String = "",
    val by: String = "",
    val at: String = "",
)

@Serializable
data class AlertRecord(
    val id: String,
    val request: AlertRequestPayload,
    @SerialName("created_at") val createdAt: String,
    val status: String = "sent",
    val deliveries: Map<String, String> = emptyMap(),
    @SerialName("acked_at") val ackedAt: String? = null,
    @SerialName("acked_by") val ackedBy: String? = null,
    val feedback: List<FeedbackItem> = emptyList(),
    @SerialName("has_thumbnail") val hasThumbnail: Boolean? = null,
    @SerialName("has_clip") val hasClip: Boolean? = null,
) {
    val title: String get() = request.title
    val body: String get() = request.body
    val severity: AlertSeverity get() = request.severity
    val topicId: String get() = request.topicId
    val isAcknowledged: Boolean get() = !ackedAt.isNullOrBlank()
    val latestFeedback: FeedbackItem? get() = feedback.lastOrNull()
    val thumbnailAvailable: Boolean
        get() = hasThumbnail ?: (request.bundleId != null || request.clipRef != null)
    val clipAvailable: Boolean
        get() = hasClip ?: (request.bundleId != null || request.clipRef != null)
}

@Serializable
data class FeedbackResponse(
    val feedback: FeedbackItem,
    val pinned: Boolean,
    val alert: AlertRecord,
)

@Serializable
data class DeviceRegistrationRequest(
    val platform: String = "android",
    val token: String,
    val name: String = "",
)

@Serializable
data class DeviceRegistrationResponse(
    val id: String,
    val platform: String,
    val name: String = "",
    @SerialName("registered_at") val registeredAt: String = "",
)

@Serializable
data class AckRequest(
    val by: String? = null,
)

@Serializable
data class FeedbackSubmitRequest(
    val rating: FeedbackRating,
    val note: String = "",
    val by: String = "",
)

@Serializable
data class HealthResponse(
    val ok: Boolean,
    val sinks: List<String> = emptyList(),
)

data class ConnectionTestResult(
    val sinks: List<String>,
    val alertCountChecked: Int,
)

data class SentinelConfig(
    val baseUrl: String = "",
    val householdToken: String = "",
    val deviceName: String = "Android Device",
) {
    fun normalizedBaseUrl(): String? {
        val trimmed = baseUrl.trim().trimEnd('/')
        if (trimmed.isEmpty()) return null
        val lower = trimmed.lowercase()
        if (!lower.startsWith("http://") && !lower.startsWith("https://")) return null
        val afterScheme = trimmed.substringAfter("://")
        if (afterScheme.isBlank() || afterScheme.startsWith("/")) return null
        return trimmed
    }

    val isConfigured: Boolean
        get() = normalizedBaseUrl() != null && householdToken.trim().isNotEmpty()
}

/**
 * Structured representation of an incoming FCM HTTP v1 data-only message
 * emitted by Vision Sentinel's `FcmSink`.
 */
data class FcmAlertPayload(
    val alertId: String,
    val topicId: String,
    val severity: AlertSeverity,
    val title: String,
    val body: String,
    val channelId: String,
    val bundleId: String?,
    val requiresAck: Boolean,
    val hasThumbnail: Boolean,
    val hasClip: Boolean,
) {
    companion object {
        const val CHANNEL_DEFAULT = "sentinel_default"
        const val CHANNEL_URGENT = "sentinel_urgent"

        fun fromData(
            data: Map<String, String>,
            fallbackTitle: String? = null,
            fallbackBody: String? = null,
        ): FcmAlertPayload? {
            val alertId = data["alert_id"]?.trim().orEmpty()
            if (alertId.isEmpty()) return null
            val severity = AlertSeverity.fromWire(data["severity"])
            val defaultChannel = if (severity == AlertSeverity.URGENT) CHANNEL_URGENT else CHANNEL_DEFAULT
            val channelId = data["channel_id"]?.takeIf { it.isNotBlank() } ?: defaultChannel
            val topicId = data["topic_id"]?.trim().orEmpty().ifEmpty { "general" }
            val title = data["title"]?.takeIf { it.isNotBlank() }
                ?: fallbackTitle?.takeIf { it.isNotBlank() }
                ?: "Vision Sentinel Alert"
            val body = data["body"] ?: fallbackBody.orEmpty()
            return FcmAlertPayload(
                alertId = alertId,
                topicId = topicId,
                severity = severity,
                title = title,
                body = body,
                channelId = channelId,
                bundleId = data["bundle_id"]?.takeIf { it.isNotBlank() },
                requiresAck = data["requires_ack"].equals("true", ignoreCase = true),
                hasThumbnail = data["has_thumbnail"].equals("true", ignoreCase = true),
                hasClip = data["has_clip"].equals("true", ignoreCase = true),
            )
        }
    }
}
