package com.toouur.foursquare.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.toouur.foursquare.data.repository.CheckinRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthUiState(
    val isLoading: Boolean = false,
    val isAuthenticated: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val repo: CheckinRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(AuthUiState())
    val uiState: StateFlow<AuthUiState> = _uiState.asStateFlow()

    init {
        // If a token is already stored, skip straight to authenticated
        viewModelScope.launch {
            val stored = repo.token.first()
            if (!stored.isNullOrBlank()) {
                _uiState.value = AuthUiState(isAuthenticated = true)
            }
        }
    }

    fun validateAndSave(token: String) {
        viewModelScope.launch {
            _uiState.value = AuthUiState(isLoading = true)
            val valid = repo.validateToken(token)
            if (valid) {
                repo.saveToken(token)
                _uiState.value = AuthUiState(isAuthenticated = true)
            } else {
                _uiState.value = AuthUiState(
                    error = "Token validation failed — check your token and try again."
                )
            }
        }
    }
}
