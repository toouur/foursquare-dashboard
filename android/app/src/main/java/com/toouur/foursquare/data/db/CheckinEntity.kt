package com.toouur.foursquare.data.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.toouur.foursquare.data.model.Checkin

/**
 * Room entity — raw check-in row, matching the checkins.csv schema exactly.
 * Fields are stored as-is from the Foursquare API; city/country normalization
 * happens in the transform layer before computing Stats.
 */
@Entity(
    tableName = "checkins",
    indices = [
        Index(value = ["venueId", "date"], unique = true),
        Index(value = ["date"]),
        Index(value = ["country"]),
        Index(value = ["city"]),
    ]
)
data class CheckinEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,

    // CSV columns (in CSV order)
    val date: Long,             // Unix timestamp
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
    /** Comma-separated companion names (from with_name CSV field) */
    val withName: String,
    /** Comma-separated companion user IDs (from with_id CSV field) */
    val withId: String,
)

fun CheckinEntity.toDomain(): Checkin = Checkin(
    date = date,
    venue = venue,
    venueId = venueId,
    venueUrl = venueUrl,
    city = city,
    state = state,
    country = country,
    neighborhood = neighborhood,
    lat = lat,
    lng = lng,
    address = address,
    category = category,
    shout = shout,
    sourceApp = sourceApp,
    sourceUrl = sourceUrl,
    withNames = withName.split(",").map { it.trim() }.filter { it.isNotEmpty() },
    withIds = withId.split(",").map { it.trim() }.filter { it.isNotEmpty() },
)

fun Checkin.toEntity(): CheckinEntity = CheckinEntity(
    date = date,
    venue = venue,
    venueId = venueId,
    venueUrl = venueUrl,
    city = city,
    state = state,
    country = country,
    neighborhood = neighborhood,
    lat = lat,
    lng = lng,
    address = address,
    category = category,
    shout = shout,
    sourceApp = sourceApp,
    sourceUrl = sourceUrl,
    withName = withNames.joinToString(", "),
    withId = withIds.joinToString(", "),
)
