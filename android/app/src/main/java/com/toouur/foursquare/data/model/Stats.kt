package com.toouur.foursquare.data.model

/**
 * Fully aggregated dashboard statistics — the Android equivalent of the `S` JS object
 * embedded in index.html. Produced by the metrics engine (Milestone 3).
 */
data class Stats(
    // ── Totals ──────────────────────────────────────────────────────────────────
    val total: Int,
    val dateMin: String,            // "YYYY-MM-DD"
    val dateMax: String,            // "YYYY-MM-DD"
    val uniquePlacesCount: Int,

    // ── Time distributions ───────────────────────────────────────────────────────
    /** [year_str, count] sorted ascending */
    val byYear: List<Pair<String, Int>>,
    /** [month_str "YYYY-MM", count] sorted ascending */
    val byMonth: List<Pair<String, Int>>,
    /** [hour 0-23, count] */
    val byHour: List<Pair<Int, Int>>,
    /** [dow 0=Mon..6=Sun, count] */
    val byDow: List<Pair<Int, Int>>,

    // ── Geography ────────────────────────────────────────────────────────────────
    /** [country, total_checkins] sorted by count desc */
    val countries: List<Pair<String, Int>>,
    /** [country, unique_venue_count] sorted by count desc */
    val countriesByVenues: List<Pair<String, Int>>,
    /** [city, count, primary_country] sorted by count desc */
    val cities: List<Triple<String, Int, String>>,
    /** city → averaged [lat, lng] (3 dp) */
    val cityCentroids: Map<String, Pair<Double, Double>>,
    /** country → [lat, lng, checkin_count] */
    val countryCentroids: Map<String, Triple<Double, Double, Int>>,

    // ── Venues ───────────────────────────────────────────────────────────────────
    /** Top-500 venues: [name, count, city, venue_id] */
    val venues: List<VenueEntry>,

    // ── Categories ───────────────────────────────────────────────────────────────
    /** [group_name, count] for pie/bar chart */
    val catGroups: List<Pair<String, Int>>,
    /** Category Explorer group names with data */
    val explorerCats: List<String>,
    /** group → top-50 entries [venue, city, count, venue_id] */
    val explorer: Map<String, List<ExplorerEntry>>,

    // ── Map data ─────────────────────────────────────────────────────────────────
    /** All unique venues with coords (pre-filter, use ≥5 check-ins filter in UI) */
    val uniquePlaces: List<PlaceEntry>,
    /** All check-in coordinates (for heatmap density) */
    val allCoords: List<LatLng>,
    /** Per-venue log-normalised heatmap weights [lat, lng, weight 0-1] */
    val venuesHeatmap: List<HeatmapEntry>,

    // ── Social ───────────────────────────────────────────────────────────────────
    /** Top-30 companions [name, count] */
    val companions: List<Pair<String, Int>>,

    // ── Activity ─────────────────────────────────────────────────────────────────
    /** GitHub-style heatmap: year → (date "YYYY-MM-DD" → count) */
    val heatmap: Map<String, Map<String, Int>>,
    /** Monthly discovery: [month "YYYY-MM", new_venues, revisits] */
    val discoveryRate: List<Triple<String, Int, Int>>,
    /** Venues visited in 3+ distinct years: [name, city, years, total_count] */
    val venueLoyalty: List<LoyaltyEntry>,

    // ── Trips ────────────────────────────────────────────────────────────────────
    val timeline: List<TripTimelineEntry>,
    val tripsCount: Int,

    // ── Feed ─────────────────────────────────────────────────────────────────────
    /** Last 30 check-ins, newest first */
    val recent: List<Checkin>,
)

// ── Supporting types ─────────────────────────────────────────────────────────

data class VenueEntry(
    val name: String,
    val count: Int,
    val city: String,
    val venueId: String,
)

data class ExplorerEntry(
    val venue: String,
    val city: String,
    val count: Int,
    val venueId: String,
)

data class PlaceEntry(
    val lat: Double,
    val lng: Double,
    val name: String,
)

data class HeatmapEntry(
    val lat: Double,
    val lng: Double,
    /** Log-normalised weight in [0, 1] */
    val weight: Double,
)

data class LoyaltyEntry(
    val name: String,
    val city: String,
    /** Calendar years in which this venue was visited */
    val years: List<Int>,
    val totalCount: Int,
)
