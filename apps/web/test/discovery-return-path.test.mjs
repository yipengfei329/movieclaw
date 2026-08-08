import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDiscoveryReturnPath,
  getDiscoveryReturnPath,
} from "../lib/discovery-return-path.ts";

test("构造 TMDB 与豆瓣发现详情返回路径", () => {
  assert.equal(buildDiscoveryReturnPath("tmdb", "movie", "42"), "/media/movie/42");
  assert.equal(buildDiscoveryReturnPath("tmdb", "tv", "7"), "/media/tv/7");
  assert.equal(buildDiscoveryReturnPath("douban", "movie", "db-42"), "/media/douban/db-42");
});

test("只接纳已知发现详情路由", () => {
  for (const path of [
    "/media/movie/42",
    "/media/tv/7",
    "/media/douban/1292052",
    "/media/douban/db-42",
  ]) {
    assert.equal(getDiscoveryReturnPath(path), path);
  }
});

test("拒绝外部、非详情和格式非法的返回地址", () => {
  for (const path of [
    undefined,
    "https://example.com",
    "//example.com",
    "/library/1",
    "/media/movie/not-a-number",
    "/media/douban/",
    "/media/douban/db-42?from=outside",
    "/media/douban/db-42#fragment",
    "/media/douban/a/b",
    "/media/movie/42\n",
  ]) {
    assert.equal(getDiscoveryReturnPath(path), null);
  }
});
