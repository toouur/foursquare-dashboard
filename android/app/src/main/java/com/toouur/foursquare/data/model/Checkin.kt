package com.toouur.foursquare.data.model

import java.time.LocalDateTime

/**
 * Domain model for a single Foursquare check-in, post-transform.
 * Mirrors a row from checkins.csv after city/country normalization.
 */
data class Checkin(
    /** Unix timestamp (seconds since epoch, UTC) */
    val date: Long,
    val venue: String,
    val venueId: String,
    val venueUrl: String,
    val city: String,
    val state: String,
    val country: String,
    val neighborhood: String,
    val lat: Double?,
    val lng: Double?,
    val address: String,
    val category: String,
    val shout: String,
    val sourceApp: String,
    val sourceUrl: String,
    /** Companion names (split from with_name CSV field) */
    val withNames: List<String>,
    /** Companion Foursquare user IDs (split from with_id CSV field) */
    val withIds: List<String>,
    /** Localised datetime after timezone resolution (set by metrics layer) */
    val localDateTime: LocalDateTime? = null,
    /** IANA timezone name, e.g. "Europe/Minsk" */
    val tzName: String? = null,
) {
    /** Human-readable local date string, e.g. "13 Mar 2024" */
    val displayDate: String
        get() = localDateTime?.let {
            "%d %s %d".format(
                it.dayOfMonth,
                it.month.name.lowercase().replaceFirstChar(Char::uppercase).take(3),
                it.year
            )
        } ?: ""

    /** Human-readable local time string, e.g. "14:35" */
    val displayTime: String
        get() = localDateTime?.let {
            "%02d:%02d".format(it.hour, it.minute)
        } ?: ""
}
