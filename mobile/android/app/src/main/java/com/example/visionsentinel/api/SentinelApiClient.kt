package com.example.visionsentinel.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

sealed class SentinelApiException(message: String) : Exception(message) {
    class InvalidConfig(message: String) : SentinelApiException(message)
    class Unauthorized : SentinelApiException("Unauthorized (401). Check your shared household bearer token.")
    class NotFound : SentinelApiException("Requested alert or media resource was not found (404).")
    class HttpError(val code: Int, detail: String) :
        SentinelApiException(if (detail.isBlank()) "Server error (HTTP $code)." else "HTTP $code: $detail")
    class NetworkError(message: String) : SentinelApiException("Network error: $message")
}

/**
 * OkHttp + kotlinx.serialization client for the self-hosted `vs-notify` HTTP API.
 * Never logs or exposes the household bearer token in error messages.
 */
class SentinelApiClient(
    baseUrl: String,
    householdToken: String,
    private val httpClient: OkHttpClient = defaultHttpClient(),
) {
    private val normalizedUrl: HttpUrl
    private val bearerToken: String

    init {
        val cleaned = baseUrl.trim().trimEnd('/')
        val parsed = cleaned.toHttpUrlOrNull()
            ?: throw SentinelApiException.InvalidConfig("Invalid server URL. Enter http:// or https://.")
        if (parsed.scheme != "http" && parsed.scheme != "https") {
            throw SentinelApiException.InvalidConfig("Server URL must use http:// or https://.")
        }
        val trimmedToken = householdToken.trim()
        if (trimmedToken.isEmpty()) {
            throw SentinelApiException.InvalidConfig("Shared household bearer token is required.")
        }
        normalizedUrl = parsed
        bearerToken = trimmedToken
    }

    constructor(config: SentinelConfig, httpClient: OkHttpClient = defaultHttpClient()) : this(
        baseUrl = config.normalizedBaseUrl()
            ?: throw SentinelApiException.InvalidConfig("Invalid server URL. Enter http:// or https://."),
        householdToken = config.householdToken,
        httpClient = httpClient,
    )

    suspend fun checkHealth(): HealthResponse = withContext(Dispatchers.IO) {
        val url = normalizedUrl.newBuilder().addPathSegment("healthz").build()
        val request = Request.Builder()
            .url(url)
            .get()
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(HealthResponse.serializer(), body)
    }

    /** Verifies reachability (`GET /healthz`) and bearer-token auth (`GET /v1/alerts?limit=1`). */
    suspend fun testConnection(): ConnectionTestResult {
        val health = checkHealth()
        val alerts = listAlerts(limit = 1)
        return ConnectionTestResult(sinks = health.sinks, alertCountChecked = alerts.size)
    }

    suspend fun registerDevice(
        pushToken: String,
        deviceName: String,
        platform: String = "android",
    ): DeviceRegistrationResponse = withContext(Dispatchers.IO) {
        val payload = json.encodeToString(
            DeviceRegistrationRequest.serializer(),
            DeviceRegistrationRequest(platform = platform, token = pushToken, name = deviceName),
        )
        val url = apiUrl("v1", "devices", "register")
        val request = authorizedRequestBuilder(url)
            .post(payload.toRequestBody(JSON_MEDIA_TYPE))
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(DeviceRegistrationResponse.serializer(), body)
    }

    suspend fun listAlerts(
        topicId: String? = null,
        severity: AlertSeverity? = null,
        unacked: Boolean? = null,
        limit: Int = 50,
    ): List<AlertRecord> = withContext(Dispatchers.IO) {
        val builder = apiUrl("v1", "alerts").newBuilder()
            .addQueryParameter("limit", limit.coerceIn(1, 500).toString())
        if (!topicId.isNullOrBlank()) builder.addQueryParameter("topic_id", topicId)
        if (severity != null) builder.addQueryParameter("severity", severity.wireValue())
        if (unacked != null) builder.addQueryParameter("unacked", unacked.toString())

        val request = authorizedRequestBuilder(builder.build())
            .get()
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(kotlinx.serialization.builtins.ListSerializer(AlertRecord.serializer()), body)
    }

    suspend fun getAlert(alertId: String): AlertRecord = withContext(Dispatchers.IO) {
        val request = authorizedRequestBuilder(apiUrl("v1", "alerts", alertId))
            .get()
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(AlertRecord.serializer(), body)
    }

    suspend fun acknowledgeAlert(alertId: String, by: String? = null): AlertRecord = withContext(Dispatchers.IO) {
        val payload = json.encodeToString(AckRequest.serializer(), AckRequest(by = by))
        val request = authorizedRequestBuilder(apiUrl("v1", "alerts", alertId, "ack"))
            .post(payload.toRequestBody(JSON_MEDIA_TYPE))
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(AlertRecord.serializer(), body)
    }

    suspend fun submitFeedback(
        alertId: String,
        rating: FeedbackRating,
        note: String = "",
        by: String = "",
    ): FeedbackResponse = withContext(Dispatchers.IO) {
        val payload = json.encodeToString(
            FeedbackSubmitRequest.serializer(),
            FeedbackSubmitRequest(rating = rating, note = note, by = by),
        )
        val request = authorizedRequestBuilder(apiUrl("v1", "alerts", alertId, "feedback"))
            .post(payload.toRequestBody(JSON_MEDIA_TYPE))
            .header("Accept", "application/json")
            .build()
        val body = executeString(request)
        json.decodeFromString(FeedbackResponse.serializer(), body)
    }

    suspend fun fetchThumbnail(alertId: String): ByteArray = withContext(Dispatchers.IO) {
        val request = authorizedRequestBuilder(apiUrl("v1", "alerts", alertId, "thumbnail"))
            .get()
            .header("Accept", "image/*")
            .build()
        executeBytes(request)
    }

    suspend fun fetchClip(alertId: String): ByteArray = withContext(Dispatchers.IO) {
        val request = authorizedRequestBuilder(apiUrl("v1", "alerts", alertId, "clip"))
            .get()
            .header("Accept", "video/mp4, */*")
            .build()
        executeBytes(request)
    }

    private fun apiUrl(vararg segments: String): HttpUrl {
        val builder = normalizedUrl.newBuilder()
        for (segment in segments) {
            builder.addPathSegment(segment)
        }
        return builder.build()
    }

    private fun authorizedRequestBuilder(url: HttpUrl): Request.Builder =
        Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $bearerToken")

    private fun executeString(request: Request): String =
        executeBytes(request).toString(Charsets.UTF_8)

    private fun executeBytes(request: Request): ByteArray {
        val response = try {
            httpClient.newCall(request).execute()
        } catch (e: IOException) {
            throw SentinelApiException.NetworkError(e.message ?: "Unable to reach server")
        }
        response.use { res ->
            val bytes = res.body?.bytes() ?: ByteArray(0)
            return when (res.code) {
                in 200..299 -> bytes
                401 -> throw SentinelApiException.Unauthorized()
                404 -> throw SentinelApiException.NotFound()
                else -> throw SentinelApiException.HttpError(res.code, extractDetail(bytes))
            }
        }
    }

    private fun extractDetail(bytes: ByteArray): String {
        val text = bytes.toString(Charsets.UTF_8).trim()
        if (text.isEmpty()) return ""
        return runCatching {
            json.decodeFromString(ErrorBody.serializer(), text).detail.orEmpty()
        }.getOrDefault(text.take(200))
    }

    @Serializable
    private data class ErrorBody(val detail: String? = null)

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

        val json: Json = Json {
            ignoreUnknownKeys = true
            isLenient = true
            encodeDefaults = true
        }

        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .build()
    }
}
