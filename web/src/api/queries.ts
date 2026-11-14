import { queryOptions } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

export const setupStatusQuery = queryOptions({
  queryKey: ["setup-status"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/setup");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

/** The logged-in user, or `null` when nobody is logged in. */
export const meQuery = queryOptions({
  queryKey: ["me"],
  queryFn: async () => {
    const { data, error, response } = await api.GET("/api/v1/me");
    if (response.status === 401) return null;
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

export const workspacesQuery = queryOptions({
  queryKey: ["workspaces"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/workspaces");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

export const devicesQuery = queryOptions({
  queryKey: ["devices"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/devices");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});
