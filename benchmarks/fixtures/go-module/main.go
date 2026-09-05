package main

import (
    "github.com/gin-gonic/gin"
    "go.uber.org/zap"
)

func main() {
    r := gin.Default()
    r.Run()
}
