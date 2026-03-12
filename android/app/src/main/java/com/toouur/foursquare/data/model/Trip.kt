package com.toouur.foursquare.data.model

/**
 * A detected trip: consecutive non-home check-ins with at least [minCheckins] entries.
 * Mirrors the trip dict produced by metrics.py detect_trips().
 */
data class Trip(
    val id: Int,
    val name: String,
    val startDate: String,          // "YYYY-MM-DD"
    val endDate: String,            // "YYYY-MM-DD"
    val startTs: Long,              // Unix timestamp of first check-in
    val startYear: Int,
    /** Inclusive duration in days */
    val duration: Int,
    /** All countries visited, sorted by check-in count descending */
    val countries: List<String>,
    /** All cities visited, sorted by check-in count descending */
    val cities: List<String>,
    val checkinCount: Int,
    val uniquePlaces: Int,          // Unique venue_ids
    /** Full check-in list for trip detail view */
    val checkins: List<TripCheckin>,
    /** All [lat, lng] pairs (one per check-in; used for map bounds) */
    val coords: List<LatLng>,
    /** Unique venue coordinates (one per venue_id; used for map pins) */
    val uniquePts: List<NamedLatLng>,
    /** Top 10 categories by count */
    val topCats: List<Pair<String, Int>>,
)

/** Lightweight check-in entry inside a trip (no full Checkin domain overhead). */
data class TripCheckin(
    val ts: Long,
    val date: String,               // "YYYY-MM-DD"
    val time: String,               // "HH:MM" local
    val datetime: String,           // "DD Mon YYYY, HH:MM" local
    val venue: String,
    val venueId: String,
    val city: String,
    val country: String,
    val category: String,
    val lat: Double?,
    val lng: Double?,
)

/** Summary entry for the timeline / Gantt chart view. */
data class TripTimelineEntry(
    val id: Int,
    val name: String,
    val start: String,              // "YYYY-MM-DD"
    val end: String,                // "YYYY-MM-DD"
    val days: Int,
    /** Up to 6 countries (for flag row) */
    val countries: List<String>,
    val count: Int,
    val year: Int,
)

data class LatLng(val lat: Double, val lng: Double)
data class NamedLatLng(val lat: Double, val lng: Double, val name: String)
