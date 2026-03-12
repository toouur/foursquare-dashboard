package com.toouur.foursquare

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.toouur.foursquare.ui.FoursquareNavHost
import com.toouur.foursquare.ui.theme.FoursquareDashboardTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            FoursquareDashboardTheme {
                FoursquareNavHost()
            }
        }
    }
}
