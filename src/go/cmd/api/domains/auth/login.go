package auth

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// Login handles the password-based login request and returns a JWT token.
func Login(jwtSecret []byte) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Username string `json:"username"`
			Password string `json:"password"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
			return
		}

		usersJSON := os.Getenv("AUTH_USERS_JSON")
		if usersJSON == "" {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Configuration error"})
			return
		}

		var users map[string]string
		if err := json.Unmarshal([]byte(usersJSON), &users); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Configuration error"})
			return
		}

		username := req.Username
		if username == "" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Missing username in request"})
			return
		}

		expectedPassword, ok := users[username]
		if !ok || subtle.ConstantTimeCompare([]byte(expectedPassword), []byte(req.Password)) != 1 {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "Unauthorized"})
			return
		}

		// Hash the username so arbitrary input never reaches MongoDB database names.
		// First 16 bytes (32 hex chars) of SHA-256 gives 128-bit uniqueness.
		h := sha256.Sum256([]byte(username))
		userID := hex.EncodeToString(h[:16])

		token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
			"sub": userID,
			"exp": time.Now().Add(24 * time.Hour).Unix(),
		})
		tokenString, err := token.SignedString(jwtSecret)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Token error"})
			return
		}

		c.JSON(http.StatusOK, gin.H{"token": tokenString})
	}
}
