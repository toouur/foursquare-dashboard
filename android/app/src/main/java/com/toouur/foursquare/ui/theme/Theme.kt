package com.toouur.foursquare.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val DarkColors = darkColorScheme(
    primary              = Primary,
    onPrimary            = OnPrimary,
    primaryContainer     = PrimaryContainer,
    onPrimaryContainer   = OnPrimaryContainer,
    secondary            = Gold,
    onSecondary          = OnPrimary,
    secondaryContainer   = SecondaryContainer,
    onSecondaryContainer = OnSecondaryContainer,
    background           = Background,
    onBackground         = TextPrimary,
    surface              = Surface,
    onSurface            = TextPrimary,
    surfaceVariant       = SurfaceVariant,
    onSurfaceVariant     = TextSecondary,
    outline              = Divider,
    error                = Error,
    onError              = OnError,
)

@Composable
fun FoursquareDashboardTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        typography  = AppTypography,
        content     = content,
    )
}
