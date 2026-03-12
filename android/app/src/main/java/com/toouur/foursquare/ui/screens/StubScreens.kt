package com.toouur.foursquare.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.navigation.NavController

/**
 * Placeholder screens — will be replaced milestone by milestone.
 * All accept the same (NavController, PaddingValues) signature as real screens.
 */

@Composable
fun DashboardScreen(nav: NavController, innerPadding: PaddingValues) =
    StubScreen("Dashboard — Milestone 4", innerPadding)

@Composable
fun TripsScreen(nav: NavController, innerPadding: PaddingValues) =
    StubScreen("Trips — Milestone 4", innerPadding)

@Composable
fun MapScreen(nav: NavController, innerPadding: PaddingValues) =
    StubScreen("Map + Heatmap — Milestone 5", innerPadding)

@Composable
fun FeedScreen(nav: NavController, innerPadding: PaddingValues) =
    StubScreen("Feed — Milestone 6", innerPadding)

@Composable
fun VenuesScreen(nav: NavController, innerPadding: PaddingValues) =
    StubScreen("Venues — Milestone 4", innerPadding)

@Composable
private fun StubScreen(label: String, innerPadding: PaddingValues) {
    Box(
        modifier          = Modifier.fillMaxSize().padding(innerPadding),
        contentAlignment  = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
    }
}
