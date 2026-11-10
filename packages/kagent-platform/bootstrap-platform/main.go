package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/kagent-dev/kagent/go/core/pkg/install"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("expected identities or postgres")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	config, err := clientcmd.BuildConfigFromFlags("", os.Getenv("KUBECONFIG"))
	if err != nil {
		return err
	}
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return err
	}
	installer := install.NewClient(install.CommandRunner{}, client)
	switch os.Args[1] {
	case "identities":
		return installer.PrepareSubstrate(ctx, "kagent")
	case "postgres":
		return installer.PreparePostgres(ctx, install.Postgres{
			Namespace: "kagent",
			Image:     "postgres:18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15",
		})
	default:
		return fmt.Errorf("unsupported bootstrap phase")
	}
}
