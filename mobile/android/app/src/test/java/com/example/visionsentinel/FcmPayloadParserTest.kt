package com.example.visionsentinel

import com.example.visionsentinel.api.AlertSeverity
import com.example.visionsentinel.api.FcmAlertPayload
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FcmPayloadParserTest {
    @Test
    fun parsesUrgentDataOnlyPayloadWithUrgentChannel() {
        val payload = FcmAlertPayload.fromData(
            mapOf(
                "alert_id" to "alert_99",
                "topic_id" to "garage-door",
                "severity" to "urgent",
                "title" to "Garage door left open",
                "body" to "Door has been open for 15 minutes.",
                "channel_id" to "sentinel_urgent",
                "bundle_id" to "bundle_123",
                "requires_ack" to "true",
                "has_thumbnail" to "true",
                "has_clip" to "true",
            ),
        )!!

        assertEquals("alert_99", payload.alertId)
        assertEquals("garage-door", payload.topicId)
        assertEquals(AlertSeverity.URGENT, payload.severity)
        assertEquals("sentinel_urgent", payload.channelId)
        assertEquals("bundle_123", payload.bundleId)
        assertTrue(payload.requiresAck)
        assertTrue(payload.hasThumbnail)
        assertTrue(payload.hasClip)
    }

    @Test
    fun defaultsChannelFromSeverityWhenOmitted() {
        val standard = FcmAlertPayload.fromData(
            mapOf("alert_id" to "a1", "severity" to "info", "title" to "Motion"),
        )!!
        assertEquals(FcmAlertPayload.CHANNEL_DEFAULT, standard.channelId)
        assertFalse(standard.requiresAck)

        val urgent = FcmAlertPayload.fromData(
            mapOf("alert_id" to "a2", "severity" to "urgent", "title" to "Urgent"),
        )!!
        assertEquals(FcmAlertPayload.CHANNEL_URGENT, urgent.channelId)
    }

    @Test
    fun returnsNullWhenAlertIdMissing() {
        assertNull(FcmAlertPayload.fromData(mapOf("title" to "No ID")))
    }
}
