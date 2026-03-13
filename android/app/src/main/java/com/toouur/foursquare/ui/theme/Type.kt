package com.toouur.foursquare.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// System fonts (downloadable font support can be wired in a later milestone)
val AppTypography = Typography(
    headlineLarge = TextStyle(
        fontFamily  = FontFamily.Serif,
        fontWeight  = FontWeight.Black,
        fontSize    = 32.sp,
        lineHeight  = 40.sp,
        color       = TextPrimary,
    ),
    headlineMedium = TextStyle(
        fontFamily  = FontFamily.Serif,
        fontWeight  = FontWeight.Bold,
        fontSize    = 24.sp,
        lineHeight  = 32.sp,
        color       = TextPrimary,
    ),
    titleLarge = TextStyle(
        fontFamily  = FontFamily.SansSerif,
        fontWeight  = FontWeight.SemiBold,
        fontSize    = 20.sp,
        lineHeight  = 28.sp,
        color       = TextPrimary,
    ),
    titleMedium = TextStyle(
        fontFamily  = FontFamily.SansSerif,
        fontWeight  = FontWeight.Medium,
        fontSize    = 16.sp,
        lineHeight  = 24.sp,
        color       = TextPrimary,
    ),
    bodyLarge = TextStyle(
        fontFamily  = FontFamily.SansSerif,
        fontWeight  = FontWeight.Normal,
        fontSize    = 16.sp,
        lineHeight  = 24.sp,
        color       = TextPrimary,
    ),
    bodyMedium = TextStyle(
        fontFamily  = FontFamily.SansSerif,
        fontWeight  = FontWeight.Normal,
        fontSize    = 14.sp,
        lineHeight  = 20.sp,
        color       = TextSecondary,
    ),
    labelSmall = TextStyle(
        fontFamily  = FontFamily.SansSerif,
        fontWeight  = FontWeight.Medium,
        fontSize    = 11.sp,
        lineHeight  = 16.sp,
        letterSpacing = 0.5.sp,
        color       = TextSecondary,
    ),
)
