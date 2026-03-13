package com.toouur.foursquare.data.api

import com.toouur.foursquare.data.db.CheckinEntity
import retrofit2.http.GET
import retrofit2.http.Query

/** Foursquare API v2 — same endpoint used by fetch_checkins.py. */
interface FoursquareApiService {

    /**
     * Fetch a page of check-ins.
     *
     * @param token  OAuth token (required)
     * @param v      API version date string, e.g. "20231201"
     * @param limit  Page size (max 250)
     * @param offset Pagination offset
     * @param afterTimestamp  Unix timestamp — return only check-ins after this time
     *                         (used for incremental sync; omit for full fetch)
     */
    @GET("users/self/checkins")
    suspend fun getCheckins(
        @Query("oauth_token")      token: String,
        @Query("v")                v: String = API_VERSION,
        @Query("limit")            limit: Int = PAGE_SIZE,
        @Query("offset")           offset: Int = 0,
        @Query("afterTimestamp")   afterTimestamp: Long? = null,
        @Query("sort")             sort: String = "newestfirst",
    ): FsqResponse

    companion object {
        const val BASE_URL    = "https://api.foursquare.com/v2/"
        const val API_VERSION = "20231201"
        const val PAGE_SIZE   = 250
    }
}

/** Map a raw API check-in to a Room entity ready for insertion. */
fun FsqCheckin.toEntity(): CheckinEntity {
    val v = venue
    val loc = v?.location
    val primaryCat = v?.categories?.firstOrNull { it.primary }?.name
        ?: v?.categories?.firstOrNull()?.name
        ?: ""

    return CheckinEntity(
        date        = createdAt,
        venue       = v?.name ?: "",
        venueId     = v?.id ?: id,
        venueUrl    = v?.let { "https://foursquare.com/v/${it.id}" } ?: "",
        city        = loc?.city ?: "",
        state       = loc?.state ?: "",
        country     = loc?.country ?: "",
        neighborhood = loc?.neighborhood ?: "",
        lat         = loc?.lat,
        lng         = loc?.lng,
        address     = loc?.address ?: "",
        category    = primaryCat,
        shout       = shout,
        sourceApp   = source?.name ?: "Foursquare",
        sourceUrl   = source?.url ?: "",
        withName    = with.joinToString(", ") { it.displayName },
        withId      = with.joinToString(", ") { it.id },
    )
}
