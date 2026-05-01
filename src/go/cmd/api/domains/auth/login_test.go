package auth

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"

	apitesting "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/golang-jwt/jwt/v5"
)

func TestLogin(t *testing.T) {
	type testCase struct {
		name          string
		requestBody   string
		authUsersJSON *string
		jwtSecret     []byte
		expectStatus  int
		expectError   string
		expectToken   bool
		expectSub     string
	}

	authUsersJSON := `{"admin":"correct-password","another-user":"another-password"}`
	wrongPassword := "wrong-password"

	tests := []testCase{
		{
			name:         "invalid json returns bad request",
			requestBody:  `{"password":`,
			jwtSecret:    []byte("jwt-secret"),
			expectStatus: http.StatusBadRequest,
			expectError:  "Invalid request",
		},
		{
			name:         "missing auth users env returns configuration error",
			requestBody:  `{"username":"admin","password":"correct-password"}`,
			jwtSecret:    []byte("jwt-secret"),
			expectStatus: http.StatusInternalServerError,
			expectError:  "Configuration error",
		},
		{
			name:          "invalid auth users env returns configuration error",
			requestBody:   `{"username":"admin","password":"correct-password"}`,
			authUsersJSON: ptrString("{"),
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusInternalServerError,
			expectError:   "Configuration error",
		},
		{
			name:          "missing username returns bad request",
			requestBody:   `{"password":"correct-password"}`,
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusBadRequest,
			expectError:   "Missing username in request",
		},
		{
			name:          "unknown username returns unauthorized",
			requestBody:   `{"username":"missing-user","password":"correct-password"}`,
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusUnauthorized,
			expectError:   "Unauthorized",
		},
		{
			name:          "empty password returns unauthorized",
			requestBody:   `{"username":"admin","password":""}`,
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusUnauthorized,
			expectError:   "Unauthorized",
		},
		{
			name:          "wrong password returns unauthorized",
			requestBody:   fmt.Sprintf(`{"username":"admin","password":"%s"}`, wrongPassword),
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusUnauthorized,
			expectError:   "Unauthorized",
		},
		{
			name:          "empty signing key still returns signed jwt",
			requestBody:   `{"username":"admin","password":"correct-password"}`,
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte{},
			expectStatus:  http.StatusOK,
			expectToken:   true,
			expectSub:     expectedSubForUsername("admin"),
		},
		{
			name:          "valid credentials return signed jwt",
			requestBody:   `{"username":"admin","password":"correct-password"}`,
			authUsersJSON: &authUsersJSON,
			jwtSecret:     []byte("jwt-secret"),
			expectStatus:  http.StatusOK,
			expectToken:   true,
			expectSub:     expectedSubForUsername("admin"),
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			setAuthUsersJSONForTest(t, tc.authUsersJSON)

			req, err := http.NewRequest(http.MethodPost, "/api/login", bytes.NewBufferString(tc.requestBody))
			if err != nil {
				t.Fatalf("failed to create request: %v", err)
			}
			req.Header.Set("Content-Type", "application/json")

			ctx, recorder := apitesting.CreateGinTestContext(http.MethodPost, "/api/login", req)
			handler := Login(tc.jwtSecret)
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

			validateJWTToken(t, tokenString, tc.jwtSecret, tc.expectSub)
		})
	}
}

func ptrString(v string) *string {
	return &v
}

func setAuthUsersJSONForTest(t *testing.T, authUsersJSON *string) {
	t.Helper()

	originalValue, wasSet := os.LookupEnv("AUTH_USERS_JSON")
	t.Cleanup(func() {
		if wasSet {
			_ = os.Setenv("AUTH_USERS_JSON", originalValue)
			return
		}
		_ = os.Unsetenv("AUTH_USERS_JSON")
	})

	if authUsersJSON == nil {
		if err := os.Unsetenv("AUTH_USERS_JSON"); err != nil {
			t.Fatalf("failed to unset AUTH_USERS_JSON: %v", err)
		}
		return
	}

	if err := os.Setenv("AUTH_USERS_JSON", *authUsersJSON); err != nil {
		t.Fatalf("failed to set AUTH_USERS_JSON: %v", err)
	}
}

func expectedSubForUsername(username string) string {
	h := sha256.Sum256([]byte(username))
	return hex.EncodeToString(h[:16])
}

func setAdminPasswordForTest(t *testing.T, adminPassword *string) {
	t.Helper()

	originalValue, wasSet := os.LookupEnv("ADMIN_PASSWORD")
	t.Cleanup(func() {
		if wasSet {
			_ = os.Setenv("ADMIN_PASSWORD", originalValue)
			return
		}
		_ = os.Unsetenv("ADMIN_PASSWORD")
	})

	if adminPassword == nil {
		if err := os.Unsetenv("ADMIN_PASSWORD"); err != nil {
			t.Fatalf("failed to unset ADMIN_PASSWORD: %v", err)
		}
		return
	}

	if err := os.Setenv("ADMIN_PASSWORD", *adminPassword); err != nil {
		t.Fatalf("failed to set ADMIN_PASSWORD: %v", err)
	}
}

func validateJWTToken(t *testing.T, tokenString string, jwtSecret []byte, expectedSub string) {
	t.Helper()

	parsedToken, err := jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
		if token.Method.Alg() != jwt.SigningMethodHS256.Alg() {
			return nil, fmt.Errorf("unexpected signing method: %s", token.Method.Alg())
		}
		return jwtSecret, nil
	})
	if err != nil {
		t.Fatalf("failed to parse token: %v", err)
	}
	if !parsedToken.Valid {
		t.Fatal("token is not valid")
	}

	claims, ok := parsedToken.Claims.(jwt.MapClaims)
	if !ok {
		t.Fatalf("unexpected token claims type: %T", parsedToken.Claims)
	}

	subClaim, ok := claims["sub"].(string)
	if !ok {
		t.Fatalf("sub claim missing or not string: %#v", claims["sub"])
	}
	if subClaim != expectedSub {
		t.Fatalf("unexpected sub claim: got %q, want %q", subClaim, expectedSub)
	}

	expClaim, ok := claims["exp"].(float64)
	if !ok {
		t.Fatalf("exp claim missing or not numeric: %#v", claims["exp"])
	}

	now := time.Now().Unix()
	expectedExp := now + int64(24*time.Hour.Seconds())
	actualExp := int64(expClaim)
	toleranceSeconds := int64(120)
	if actualExp < expectedExp-toleranceSeconds || actualExp > expectedExp+toleranceSeconds {
		t.Fatalf("exp claim out of expected range: got %d, expected around %d (+/-%d sec)", actualExp, expectedExp, toleranceSeconds)
	}
}
