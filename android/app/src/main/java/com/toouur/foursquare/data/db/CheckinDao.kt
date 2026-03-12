package com.toouur.foursquare.data.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface CheckinDao {

    /** Upsert by (venueId, date) unique index — safe for incremental syncs. */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertAll(checkins: List<CheckinEntity>)

    @Query("SELECT * FROM checkins ORDER BY date ASC")
    suspend fun getAll(): List<CheckinEntity>

    @Query("SELECT * FROM checkins ORDER BY date ASC")
    fun observeAll(): Flow<List<CheckinEntity>>

    @Query("SELECT MAX(date) FROM checkins")
    suspend fun latestTimestamp(): Long?

    @Query("SELECT COUNT(*) FROM checkins")
    fun observeCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM checkins")
    suspend fun count(): Int

    /** Recent N check-ins, newest first — used by Feed screen. */
    @Query("SELECT * FROM checkins ORDER BY date DESC LIMIT :limit")
    fun observeRecent(limit: Int = 30): Flow<List<CheckinEntity>>

    @Query("DELETE FROM checkins")
    suspend fun deleteAll()
}
