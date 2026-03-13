package com.toouur.foursquare.data.db

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [CheckinEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun checkinDao(): CheckinDao
}
