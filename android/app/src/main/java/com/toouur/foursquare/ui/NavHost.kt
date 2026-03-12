package com.toouur.foursquare.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.toouur.foursquare.ui.auth.AuthScreen
import com.toouur.foursquare.ui.screens.DashboardScreen
import com.toouur.foursquare.ui.screens.FeedScreen
import com.toouur.foursquare.ui.screens.MapScreen
import com.toouur.foursquare.ui.screens.TripsScreen
import com.toouur.foursquare.ui.screens.VenuesScreen

sealed class Screen(val route: String, val label: String) {
    object Dashboard : Screen("dashboard", "Dashboard")
    object Trips     : Screen("trips",     "Trips")
    object Map       : Screen("map",       "Map")
    object Feed      : Screen("feed",      "Feed")
    object Venues    : Screen("venues",    "Venues")
    object Auth      : Screen("auth",      "Auth")
}

private val bottomNavItems = listOf(
    Screen.Dashboard to Icons.Default.Home,
    Screen.Trips     to Icons.Default.List,
    Screen.Map       to Icons.Default.Map,
    Screen.Feed      to Icons.Default.Place,
    Screen.Venues    to Icons.Default.People,
)

@Composable
fun FoursquareNavHost() {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDest = navBackStackEntry?.destination
    val showBottomBar = currentDest?.route != Screen.Auth.route

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    bottomNavItems.forEach { (screen, icon) ->
                        val selected = currentDest?.hierarchy?.any { it.route == screen.route } == true
                        NavigationBarItem(
                            icon     = { Icon(icon, contentDescription = screen.label) },
                            label    = { Text(screen.label) },
                            selected = selected,
                            onClick  = {
                                navController.navigate(screen.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        )
                    }
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController    = navController,
            startDestination = Screen.Dashboard.route,
            // padding passed to each screen so content clears the nav bar
        ) {
            composable(Screen.Auth.route)      { AuthScreen(navController, innerPadding) }
            composable(Screen.Dashboard.route) { DashboardScreen(navController, innerPadding) }
            composable(Screen.Trips.route)     { TripsScreen(navController, innerPadding) }
            composable(Screen.Map.route)       { MapScreen(navController, innerPadding) }
            composable(Screen.Feed.route)      { FeedScreen(navController, innerPadding) }
            composable(Screen.Venues.route)    { VenuesScreen(navController, innerPadding) }
        }
    }
}
