package com.toouur.foursquare.ui.auth

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavController
import com.toouur.foursquare.ui.Screen

/**
 * Token entry screen — Milestone 1.
 * Validates the token against the Foursquare API before saving.
 */
@Composable
fun AuthScreen(
    navController: NavController,
    innerPadding: PaddingValues,
    vm: AuthViewModel = hiltViewModel(),
) {
    val uiState by vm.uiState.collectAsState()
    var tokenText by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf(false) }

    // Navigate away on successful save
    LaunchedEffect(uiState.isAuthenticated) {
        if (uiState.isAuthenticated) {
            navController.navigate(Screen.Dashboard.route) {
                popUpTo(Screen.Auth.route) { inclusive = true }
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text  = "Check-in Journal",
            style = MaterialTheme.typography.headlineLarge,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text  = "Enter your Foursquare OAuth token",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(32.dp))

        OutlinedTextField(
            value         = tokenText,
            onValueChange = { tokenText = it },
            label         = { Text("OAuth Token") },
            singleLine    = true,
            visualTransformation = if (showToken) VisualTransformation.None
                                   else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            trailingIcon  = {
                IconButton(onClick = { showToken = !showToken }) {
                    Icon(
                        if (showToken) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                        contentDescription = if (showToken) "Hide token" else "Show token",
                    )
                }
            },
            isError   = uiState.error != null,
            modifier  = Modifier.fillMaxWidth(),
        )

        if (uiState.error != null) {
            Spacer(Modifier.height(4.dp))
            Text(
                text  = uiState.error!!,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        Spacer(Modifier.height(24.dp))

        Button(
            onClick  = { vm.validateAndSave(tokenText) },
            enabled  = tokenText.isNotBlank() && !uiState.isLoading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (uiState.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                )
            } else {
                Text("Connect")
            }
        }

        Spacer(Modifier.height(16.dp))
        Text(
            text  = "Get your token at foursquare.com/developers",
            style = MaterialTheme.typography.labelSmall,
        )
    }
}
