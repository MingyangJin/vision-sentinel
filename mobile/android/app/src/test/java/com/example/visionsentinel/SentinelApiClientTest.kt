package com.example.visionsentinel

import com.example.visionsentinel.api.AlertSeverity
import com.example.visionsentinel.api.FeedbackRating
import com.example.visionsentinel.api.SentinelApiClient
import com.example.visionsentinel.api.SentinelApiException
import com.example.visionsentinel.api.SentinelConfig
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class SentinelApiClientTest {
    private lateinit var server: MockWebServer
    private val token = "test-household-token-android"

    private val sampleAlertJson = """
        {
          "id": "alert_android_1",
          "request": {
            "topic_id": "front-porch",
            "severity": "urgent",
            "title": "Package delivered",
            "body": "Left near the doorway.",
            "clip_ref": "cam1/clip.mp4",
            "dedupe_key": "parcel",
            "cooldown_sec": 300.0,
            "requires_ack": true,
            "bundle_id": "20261125-160000-001",
            "camera_ids": ["porch-cam"],
            "source": "main_agent",
            "rubric_version": "9f86d081",
            "tool_versions": {"motion.py": "abc123"},
            "speaker": false
          },
          "created_at": "2026-11-25T16:25:40.123456+00:00",
          "status": "sent",
          "deliveries": {"fcm": "ok"},
          "acked_at": null,
          "acked_by": null,
          "feedback": [],
          "has_thumbnail": true,
          "has_clip": true
        }
    """.trimIndent()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun client(): SentinelApiClient =
        SentinelApiClient(server.url("/").toString(), token)

    @Test
    fun configNormalizationAndValidation() {
        val valid = SentinelConfig(" https://sentinel.example.com:8790/// ", "tok")
        assertEquals("https://sentinel.example.com:8790", valid.normalizedBaseUrl())
        assertTrue(valid.isConfigured)

        val invalid = SentinelConfig("ftp://example.com", "tok")
        assertNull(invalid.normalizedBaseUrl())
        assertFalse(invalid.isConfigured)

        val noToken = SentinelConfig("https://sentinel.example.com", "  ")
        assertFalse(noToken.isConfigured)
    }

    @Test
    fun testConnectionVerifiesHealthzAndBearerAuth() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true,"sinks":["fcm","desktop"]}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("[$sampleAlertJson]"))

        val result = client().testConnection()
        assertEquals(listOf("fcm", "desktop"), result.sinks)
        assertEquals(1, result.alertCountChecked)

        val healthReq = server.takeRequest()
        assertEquals("/healthz", healthReq.path)
        assertNull(healthReq.getHeader("Authorization"))

        val alertsReq = server.takeRequest()
        assertEquals("/v1/alerts?limit=1", alertsReq.path)
        assertEquals("Bearer $token", alertsReq.getHeader("Authorization"))
    }

    @Test
    fun registerDevicePostsAndroidPlatformAndBearerHeader() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"id":"dev_99","platform":"android","name":"Sample Phone","registered_at":"2026-11-25T16:30:00+00:00"}""",
            ),
        )

        val resp = client().registerDevice(pushToken = "fcm_token_xyz", deviceName = "Sample Phone")
        assertEquals("dev_99", resp.id)
        assertEquals("android", resp.platform)

        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/v1/devices/register", req.path)
        assertEquals("Bearer $token", req.getHeader("Authorization"))
        val body = req.body.readUtf8()
        assertTrue(body.contains("\"platform\":\"android\""))
        assertTrue(body.contains("\"token\":\"fcm_token_xyz\""))
        assertTrue(body.contains("\"name\":\"Sample Phone\""))
    }

    @Test
    fun listAckFeedbackAndMediaEndpointsUseBearerAuth() = runTest {
        val ackedJson = sampleAlertJson
            .replace("\"acked_at\": null", "\"acked_at\": \"2026-11-25T16:26:00+00:00\"")
            .replace("\"acked_by\": null", "\"acked_by\": \"Sample Phone\"")

        val feedbackResp = """
            {
              "feedback": {
                "alert_id": "alert_android_1",
                "rating": "down",
                "note": "Tree shadow",
                "by": "Sample Phone",
                "at": "2026-11-25T16:27:00+00:00"
              },
              "pinned": true,
              "alert": $ackedJson
            }
        """.trimIndent()

        server.enqueue(MockResponse().setResponseCode(200).setBody("[$sampleAlertJson]"))
        server.enqueue(MockResponse().setResponseCode(200).setBody(ackedJson))
        server.enqueue(MockResponse().setResponseCode(200).setBody(feedbackResp))
        server.enqueue(MockResponse().setResponseCode(200).setBody(okio.Buffer().write(byteArrayOf(0x01, 0x02, 0x03))))
        server.enqueue(MockResponse().setResponseCode(200).setBody(okio.Buffer().write(byteArrayOf(0x04, 0x05, 0x06))))

        val c = client()
        val alerts = c.listAlerts(topicId = "front-porch", severity = AlertSeverity.URGENT, unacked = true, limit = 20)
        assertEquals(1, alerts.size)
        assertEquals("alert_android_1", alerts[0].id)
        assertTrue(alerts[0].thumbnailAvailable)
        assertTrue(alerts[0].clipAvailable)

        val acked = c.acknowledgeAlert("alert_android_1", by = "Sample Phone")
        assertTrue(acked.isAcknowledged)
        assertEquals("Sample Phone", acked.ackedBy)

        val fb = c.submitFeedback("alert_android_1", rating = FeedbackRating.DOWN, note = "Tree shadow", by = "Sample Phone")
        assertTrue(fb.pinned)
        assertEquals(FeedbackRating.DOWN, fb.feedback.rating)

        val thumb = c.fetchThumbnail("alert_android_1")
        assertArrayEquals(byteArrayOf(0x01, 0x02, 0x03), thumb)

        val clip = c.fetchClip("alert_android_1")
        assertArrayEquals(byteArrayOf(0x04, 0x05, 0x06), clip)

        val listReq = server.takeRequest()
        assertEquals("/v1/alerts?limit=20&topic_id=front-porch&severity=urgent&unacked=true", listReq.path)
        assertEquals("Bearer $token", listReq.getHeader("Authorization"))

        val ackReq = server.takeRequest()
        assertEquals("/v1/alerts/alert_android_1/ack", ackReq.path)
        assertEquals("Bearer $token", ackReq.getHeader("Authorization"))

        val fbReq = server.takeRequest()
        assertEquals("/v1/alerts/alert_android_1/feedback", fbReq.path)
        assertTrue(fbReq.body.readUtf8().contains("\"rating\":\"down\""))

        val thumbReq = server.takeRequest()
        assertEquals("/v1/alerts/alert_android_1/thumbnail", thumbReq.path)
        assertEquals("Bearer $token", thumbReq.getHeader("Authorization"))

        val clipReq = server.takeRequest()
        assertEquals("/v1/alerts/alert_android_1/clip", clipReq.path)
        assertEquals("Bearer $token", clipReq.getHeader("Authorization"))
    }

    @Test
    fun unauthorizedMapsToTypedExceptionWithoutLeakingToken() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"invalid token"}"""))
        try {
            client().listAlerts()
            fail("Expected Unauthorized exception")
        } catch (e: SentinelApiException.Unauthorized) {
            assertFalse(e.message.orEmpty().contains(token))
        }
    }
}
