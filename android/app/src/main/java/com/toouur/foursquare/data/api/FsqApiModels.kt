package com.toouur.foursquare.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Root envelope from GET /v2/users/self/checkins */
@Serializable
data class FsqResponse(
    val meta: Meta,
    val response: CheckinsWrapper,
)

@Serializable
data class Meta(
    val code: Int,
    @SerialName("errorDetail") val errorDetail: String = "",
)

@Serializable
data class CheckinsWrapper(
    val checkins: CheckinsPaged,
)

@Serializable
data class CheckinsPaged(
    val count: Int,
    val items: List<FsqCheckin>,
)

@Serializable
data class FsqCheckin(
    val id: String,
    val createdAt: Long,
    val venue: FsqVenue? = null,
    val shout: String = "",
    @SerialName("with") val with: List<FsqUser> = emptyList(),
    val source: FsqSource? = null,
)

@Serializable
data class FsqVenue(
    val id: String,
    val name: String,
    val location: FsqLocation? = null,
    val categories: List<FsqCategory> = emptyList(),
    val url: String = "",
)

@Serializable
data class FsqLocation(
    val city: String = "",
    val state: String = "",
    val country: String = "",
    val address: String = "",
    val neighborhood: String = "",
    val lat: Double? = null,
    val lng: Double? = null,
)

@Serializable
data class FsqCategory(
    val id: String = "",
    val name: String,
    val primary: Boolean = false,
)

@Serializable
data class FsqUser(
    val id: String,
    @SerialName("firstName") val firstName: String = "",
    @SerialName("lastName") val lastName: String = "",
) {
    val displayName: String
        get() = "$firstName $lastName".trim()
}

@Serializable
data class FsqSource(
    val name: String = "",
    val url: String = "",
)
