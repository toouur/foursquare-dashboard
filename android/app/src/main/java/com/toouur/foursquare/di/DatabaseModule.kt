package com.toouur.foursquare.di

import android.content.Context
import androidx.room.Room
import com.toouur.foursquare.data.db.AppDatabase
import com.toouur.foursquare.data.db.CheckinDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext ctx: Context): AppDatabase =
        Room.databaseBuilder(ctx, AppDatabase::class.java, "foursquare.db")
            .fallbackToDestructiveMigration()
            .build()

    @Provides
    fun provideCheckinDao(db: AppDatabase): CheckinDao = db.checkinDao()
}
