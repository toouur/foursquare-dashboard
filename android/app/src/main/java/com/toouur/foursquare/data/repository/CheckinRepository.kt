package com.toouur.foursquare.data.repository

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.toouur.foursquare.data.api.FoursquareApiService
import com.toouur.foursquare.data.api.toEntity
import com.toouur.foursquare.data.db.CheckinDao
import com.toouur.foursquare.data.db.CheckinEntity
import com.toouur.foursquare.data.db.toDomain
import com.toouur.foursquare.data.model.Checkin
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore by preferencesDataStore(name = "prefs")

/**
 * Central data access layer.
 * - Wraps Room for local storage.
 * - Drives incremental and full fetches against the Foursquare v2 API.
 * - Persists the OAuth token in DataStore (clear-text; token is not a secret
 *   in the same sense as a password — it's user-delegated access to their own data).
 */
@Singleton
class CheckinRepository @Inject constructor(
    @ApplicationContext private val ctx: Context,
    private val api: FoursquareApiService,
    private val dao: CheckinDao,
) {
    companion object {
        private val KEY_TOKEN = stringPreferencesKey("fsq_token")
        /** Polite delay between paginated API requests (mirrors fetch_checkins.py SLEEP) */
        private const val PAGE_DELAY_MS = 350L
    }

    // ── Token ─────────────────────────────────────────────────────────────────

    val token: Flow<String?> = ctx.dataStore.data.map { it[KEY_TOKEN] }

    suspend fun saveToken(token: String) {
        ctx.dataStore.edit { it[KEY_TOKEN] = token.trim() }
    }

    suspend fun clearToken() {
        ctx.dataStore.edit { it.remove(KEY_TOKEN) }
    }

    /** Validate token by doing a minimal API call (limit=1). Returns true if HTTP 200. */
    suspend fun validateToken(token: String): Boolean = runCatching {
        val resp = api.getCheckins(token = token, limit = 1)
        resp.meta.code == 200
    }.getOrDefault(false)

    // ── Checkins ─────────────────────────────────────────────────────────────

    fun observeAll(): Flow<List<CheckinEntity>> = dao.observeAll()

    fun observeCount(): Flow<Int> = dao.observeCount()

    suspend fun getAllDomain(): List<Checkin> = dao.getAll().map { it.toDomain() }

    // ── Sync ─────────────────────────────────────────────────────────────────

    /**
     * Incremental sync: fetch only check-ins newer than the latest stored timestamp.
     * Falls back to full fetch if the database is empty.
     *
     * @return number of new check-ins inserted
     */
    suspend fun syncIncremental(): Int {
        val tok = token.first() ?: return 0
        val since = dao.latestTimestamp()
        return if (since == null) syncFull() else fetchAndStore(tok, afterTimestamp = since)
    }

    /**
     * Full fetch: clears the database and re-fetches the entire history.
     * Mirrors `--full` mode in fetch_checkins.py.
     *
     * @return total check-ins stored
     */
    suspend fun syncFull(): Int {
        val tok = token.first() ?: return 0
        dao.deleteAll()
        return fetchAndStore(tok, afterTimestamp = null)
    }

    /**
     * Core pagination loop — mirrors fetch_checkins.py fetch_all().
     * Handles rate-limits (HTTP 500 → retry up to 3×) and paginates until exhausted.
     */
    private suspend fun fetchAndStore(token: String, afterTimestamp: Long?): Int {
        var offset = 0
        var inserted = 0

        while (true) {
            val resp = retryOnServerError {
                api.getCheckins(
                    token = token,
                    offset = offset,
                    afterTimestamp = afterTimestamp,
                )
            } ?: break

            if (resp.meta.code != 200) break

            val items = resp.response.checkins.items
            if (items.isEmpty()) break

            val entities = items.map { it.toEntity() }
            dao.insertAll(entities)
            inserted += entities.size

            if (items.size < FoursquareApiService.PAGE_SIZE) break
            offset += items.size
            delay(PAGE_DELAY_MS)
        }

        return inserted
    }

    private suspend fun <T> retryOnServerError(maxRetries: Int = 3, block: suspend () -> T): T? {
        repeat(maxRetries) { attempt ->
            runCatching { return block() }.onFailure { e ->
                if (attempt < maxRetries - 1) delay(2_000L * (attempt + 1))
                else return null
            }
        }
        return null
    }
}
