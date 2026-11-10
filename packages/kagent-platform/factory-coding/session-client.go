package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	apiv1 "github.com/kagent-dev/kagent/go/api/gen/kagent/api/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/encoding/protojson"
)

func main() {
	if err := call(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func call() error {
	if len(os.Args) != 6 && len(os.Args) != 7 {
		return fmt.Errorf("expected endpoint token-file create|get|list|delete|namespaces identity agent [namespace]")
	}
	namespace := "factory-executions"
	if len(os.Args) == 7 {
		namespace = os.Args[6]
		if namespace == "" {
			return fmt.Errorf("namespace must not be empty")
		}
	}
	var data []byte
	var err error
	if os.Args[2] != "-" {
		data, err = os.ReadFile(os.Args[2])
		if err != nil {
			return err
		}
	}
	conn, err := grpc.NewClient(os.Args[1], grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	if len(data) != 0 {
		ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+strings.TrimSpace(string(data)))
	}
	client := apiv1.NewSessionServiceClient(conn)
	switch os.Args[3] {
	case "create":
		response, err := client.CreateSession(ctx, &apiv1.CreateSessionRequest{
			Agent:     &apiv1.ResourceReference{Namespace: namespace, Name: os.Args[5]},
			RequestId: os.Args[4],
		})
		if err != nil {
			return err
		}
		result, err := protojson.Marshal(response)
		if err != nil {
			return err
		}
		fmt.Println(string(result))
	case "get":
		response, err := client.GetSession(ctx, &apiv1.GetSessionRequest{SessionId: os.Args[4]})
		if err != nil {
			return err
		}
		result, err := protojson.Marshal(response)
		if err != nil {
			return err
		}
		fmt.Println(string(result))
	case "delete":
		response, err := client.DeleteSession(ctx, &apiv1.DeleteSessionRequest{SessionId: os.Args[4]})
		if err != nil {
			return err
		}
		result, err := protojson.Marshal(response)
		if err != nil {
			return err
		}
		fmt.Println(string(result))
	case "list":
		all := &apiv1.ListSessionsResponse{}
		page := &apiv1.PageRequest{Limit: 100}
		for {
			response, err := client.ListSessions(ctx, &apiv1.ListSessionsRequest{
				AllCreators: true, Agent: &apiv1.ResourceReference{Namespace: namespace, Name: os.Args[5]}, Page: page,
			})
			if err != nil {
				return err
			}
			all.Sessions = append(all.Sessions, response.Sessions...)
			next := response.GetPage().GetNextPageToken()
			if next == "" {
				break
			}
			if next == page.PageToken {
				return fmt.Errorf("session pagination did not advance")
			}
			page.PageToken = next
		}
		result, err := protojson.Marshal(all)
		if err != nil {
			return err
		}
		fmt.Println(string(result))
	case "namespaces":
		response, err := apiv1.NewSystemServiceClient(conn).ListNamespaces(ctx, &apiv1.ListNamespacesRequest{})
		if err != nil {
			return err
		}
		result, err := protojson.Marshal(response)
		if err != nil {
			return err
		}
		fmt.Println(string(result))
	default:
		return fmt.Errorf("unsupported session operation")
	}
	return nil
}
