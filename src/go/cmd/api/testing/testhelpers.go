package testing

import (
	"net/http"
	"net/http/httptest"

	"github.com/gin-gonic/gin"
)

// CreateGinTestContext builds a gin.Context and associated ResponseRecorder for tests.
func CreateGinTestContext(method, path string, body *http.Request) (*gin.Context, *httptest.ResponseRecorder) {
	w := httptest.NewRecorder()
	var req *http.Request
	if body != nil {
		req = body
	} else {
		req, _ = http.NewRequest(method, path, nil)
	}
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	return c, w
}
