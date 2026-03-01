package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"testing"

	thelpers "github.com/fabrizio2210/cover_letter/src/go/cmd/api/testing"
	"github.com/golang-jwt/jwt/v5"
	"github.com/stretchr/testify/require"
)

func TestLogin_BadRequest(t *testing.T) {
	handler := Login([]byte("secret"))
	req, _ := http.NewRequest(http.MethodPost, "/api/login", nil)
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/login", req)
	handler(ctx)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestLogin_Unauthorized(t *testing.T) {
	os.Setenv("ADMIN_PASSWORD", "secret")
	defer os.Unsetenv("ADMIN_PASSWORD")

	handler := Login([]byte("jwtsecret"))
	body := bytes.NewBufferString(`{"password":"wrong"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/login", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/login", req)
	handler(ctx)
	require.Equal(t, http.StatusUnauthorized, w.Code)
}

func TestLogin_Success_ReturnsToken(t *testing.T) {
	secret := []byte("myjwtsecret")
	os.Setenv("ADMIN_PASSWORD", "secret")
	defer os.Unsetenv("ADMIN_PASSWORD")

	handler := Login(secret)
	body := bytes.NewBufferString(`{"password":"secret"}`)
	req, _ := http.NewRequest(http.MethodPost, "/api/login", body)
	req.Header.Set("Content-Type", "application/json")
	ctx, w := thelpers.CreateGinTestContext(http.MethodPost, "/api/login", req)
	handler(ctx)

	require.Equal(t, http.StatusOK, w.Code)
	var resp map[string]string
	err := json.Unmarshal(w.Body.Bytes(), &resp)
	require.NoError(t, err)
	tokenStr, ok := resp["token"]
	require.True(t, ok)

	// verify token parses with the same secret
	parsed, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
		return secret, nil
	})
	require.NoError(t, err)
	require.NotNil(t, parsed)
	require.True(t, parsed.Valid)
}
