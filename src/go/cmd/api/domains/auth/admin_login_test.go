package auth

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	apitesting "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/golang-jwt/jwt/v5"
)

func TestAdminLogin(t *testing.T) {
	type testCase struct {
		name          string
		requestBody   string
		adminPassword *string
		jwtSecret     []byte
		expectStatus  int
		expectError   string
		expectToken   bool
	}

	password := "correct-password"
	wrongPassword := "wrong-password"

	tests := []testCase{
		{
			name:         "invalid json returns bad request",
			requestBody:  `{"password":`,
			jwtSecret:    []byte("admin-jwt-secret"),
			expectStatus: http.StatusBadRequest,
			expectError:  "Invalid request",
		},
		{
			name:         "missing admin password env returns unauthorized",
			requestBody:  fmt.Sprintf(`{"password":"%s"}`, password),
			jwtSecret:    []byte("admin-jwt-secret"),
			expectStatus: http.StatusUnauthorized,
			expectError:  "Unauthorized",
		},
		{
			name:          "wrong password returns unauthorized",
			requestBody:   fmt.Sprintf(`{"password":"%s"}`, wrongPassword),
			adminPassword: &password,
			jwtSecret:     []byte("admin-jwt-secret"),
			expectStatus:  http.StatusUnauthorized,
			expectError:   "Unauthorized",
		},
		{
			name:          "empty password returns unauthorized",
			requestBody:   `{"password":""}`,
			adminPassword: &password,
			jwtSecret:     []byte("admin-jwt-secret"),
			expectStatus:  http.StatusUnauthorized,
			expectError:   "Unauthorized",
		},
		{
			name:          "empty signing key still returns signed jwt",
			requestBody:   fmt.Sprintf(`{"password":"%s"}`, password),
			adminPassword: &password,
			jwtSecret:     []byte{},
			expectStatus:  http.StatusOK,
			expectToken:   true,
		},
		{
			name:          "valid credentials return signed jwt with admin claims",
			requestBody:   fmt.Sprintf(`{"password":"%s"}`, password),
			adminPassword: &password,
			jwtSecret:     []byte("admin-jwt-secret"),
			expectStatus:  http.StatusOK,
			expectToken:   true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			setAdminPasswordForTest(t, tc.adminPassword)

			req, err := http.NewRequest(http.MethodPost, "/api/admin/login", bytes.NewBufferString(tc.requestBody))
			if err != nil {
				t.Fatalf("failed to create request: %v", err)
			}
			req.Header.Set("Content-Type", "application/json")

			ctx, recorder := apitesting.CreateGinTestContext(http.MethodPost, "/api/admin/login", req)
			handler := AdminLogin(tc.jwtSecret)
			handler(ctx)

			if recorder.Code != tc.expectStatus {
				t.Fatalf("unexpected status code: got %d, want %d", recorder.Code, tc.expectStatus)
			}

			var payload map[string]any
			if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
				t.Fatalf("failed to unmarshal response: %v", err)
			}

			if tc.expectError != "" {
				errValue, ok := payload["error"].(string)
				if !ok {
					t.Fatalf("error field missing or not string: %#v", payload["error"])
				}
				if errValue != tc.expectError {
					t.Fatalf("unexpected error response: got %q, want %q", errValue, tc.expectError)
				}
				return
			}

			if !tc.expectToken {
				return
			}

			tokenString, ok := payload["token"].(string)
			if !ok || tokenString == "" {
				t.Fatalf("token field missing or empty: %#v", payload["token"])
			}

			validateJWTToken(t, tokenString, tc.jwtSecret)
			validateAdminJWTClaims(t, tokenString, tc.jwtSecret)
		})
	}
}

func validateAdminJWTClaims(t *testing.T, tokenString string, jwtSecret []byte) {
	t.Helper()

	parsedToken, err := jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
		if token.Method.Alg() != jwt.SigningMethodHS256.Alg() {
			return nil, fmt.Errorf("unexpected signing method: %s", token.Method.Alg())
		}
		return jwtSecret, nil
	})
	if err != nil {
		t.Fatalf("failed to parse token for claims validation: %v", err)
	}

	claims, ok := parsedToken.Claims.(jwt.MapClaims)
	if !ok {
		t.Fatalf("unexpected claims type: %T", parsedToken.Claims)
	}

	sub, ok := claims["sub"].(string)
	if !ok || sub != "admin" {
		t.Fatalf("expected sub claim to be %q, got %#v", "admin", claims["sub"])
	}

	role, ok := claims["role"].(string)
	if !ok || role != "admin" {
		t.Fatalf("expected role claim to be %q, got %#v", "admin", claims["role"])
	}
}
