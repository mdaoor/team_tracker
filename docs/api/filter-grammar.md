# Filter grammar (`tt_filter`)

One JSON document filters work items for saved views, `POST /query/*`, the layouts and (Phase 8) policy `candidate_filter`s.
Top-level keys are AND-ed; `and` / `or` / `not` compose sub-filters up to 4 levels. Unknown keys, values or names answer
`400 invalid_filter` with a JSON pointer (`/people/assignee`). The parser is `packages/filter_grammar` (pure, no database);
the SQL compiler is `tt_server.query.compile`. ACL (`visible()`) is applied last, so a shared view shows each opener only what they may see;
Inbox projects are excluded unless `include_inbox` is true (and are still only visible to their owner / line managers).

| key | shape | SQL |
|---|---|---|
| `dims` | `{<dimension_key>: "V" \| ["V"] \| {"in"\|"not_in": [...]} \| {"is_null": bool} \| {"under": "V"}}` | `EXISTS` over confirmed `classifications` joined to `dimension_values` (value key, case-insensitive); `under` walks the value tree with a recursive CTE. `stage`, `project`, `state` fall back to their dedicated clauses when no dimension of that name exists |
| `people` | `assignee`, `reporter` (handle(s) or `me`), `assignee_in_team` (team + sub-teams, by membership), `assignment_role{role,person}`, `unassigned` | columns, `memberships`, `assignments` |
| `people2` | `{reports_to: "@h", kind: [...]}` | `assignee_id IN reports_of(...)` (P4 resolver) |
| `priority` | `gte / lte / eq / in` over `lowest < low < medium < high < highest < critical` | ordinal `CASE` |
| `weight` | `gte / lte / eq`, optional `scheme` | the scheme's `weights` row (or the item's cached default weight) |
| `impact` | `gte / lte / eq` (1-5) | column |
| `due_date` `planned_start` `planned_end` `actual_end` `created` | `between [a,b]` / `before` / `after` / `is_null`; tokens `today tomorrow yesterday start_of_week end_of_week start_of_month end_of_month +Nd -Nd +Nw -Nw` or ISO dates | date columns (ADR-0010) |
| `touched` | `{since: token}` | `EXISTS events` since the start of that day in the viewer's zone |
| `state` | `{category: [open,active,done], in: [state keys]}` | `workflow_states` |
| `stage` | `{in, process, age_gt_expected}` | current `stage_placements`; `age_gt_expected` compares `entered_at` with the stage's expected days |
| `type` | `{in: [task, bug, epic ...]}` | `work_item_types` |
| `tags` | `{all, any, none}` | `tag_links` |
| `risk` | `{open_score_gte: n, linked: "KEY"}` | `risk_links` + non-closed `risks` (p x i) |
| `violations` | `{open, category[], severity_gte}` | `violations` (empty until Phase 8 writes them) |
| `text` | `"login"` | `ILIKE` on title / key, `%` and `_` escaped |
| `project` | `{in: ["WEB"]}` | `projects.key` |
| `include_inbox` | bool | see above |

Three-valued logic is hidden: an unknown comparison (a NULL due date against `before: today`) counts as *no match*, so `not` is an exact complement.

## Query API
* `POST /query/items {filter, group_by[], sort[{field,dir}], weight_scheme_id?, time_zone?, page{cursor,size<=200}}` -> `{items[], groups[], page{next_cursor,total}}` (card fields per UX 4.2).
  Sort fields: `priority key title due_date planned_end created updated impact weight stage_age`.
* `POST /query/aggregate {filter, rows, cols?, measure, weight_scheme_id?}` -> `{rows[], cols[], cells[][], totals{rows,cols,all}}`.
  Dimensions: any `dimension` key plus `stage state project type priority assignee team tags` (multi-valued: an item counts under each tag).
  Measures: `count hours weighted_throughput sum_weight open_violations avg_stage_age remaining_estimate`.
* `GET /projects/{key}/board`, `GET /processes/{key}/pipeline` (`?filter=<json>`), `POST /query/gantt`, `POST /query/calendar`, `GET /now`.
* `POST /query/items:csv`, `POST /query/aggregate:csv`: stamped (`Exported by ... scope ... at ...`).
* `/views` CRUD + `POST /views/{id}/run`; sharing needs `view.share`; running a shared view applies the *caller's* visibility.
* `GET /meta/schema/filter` serves the JSON Schema below; `POST /query/explain` (admin) returns the compiled SQL.

## Performance
`make`-style check: `apps/server/tests/api/test_query_perf.py` clones one item 50 000 times (about 200 000 classifications, a stage
placement each) and times four filters that all match every item (worst case) as an admin and a member: p95 between 63 and 255 ms
here (budget 300 ms). What mattered: ids are selected first (filter + ACL + sort + a window `count(*)` in one scan) and the card
fields are loaded only for the page; JIT is off for the app's connections (it tripled the scan time); the current-stage lookup is
restricted to the page's ids.

## JSON Schema
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://teamtracker.local/schemas/filter.json",
  "title": "Team Tracker filter",
  "description": "Top-level keys are AND-ed. and/or/not nest up to 4 levels (enforced by the parser).",
  "type": "object",
  "properties": {
    "dims": {
      "type": "object",
      "additionalProperties": {
        "oneOf": [
          {
            "type": "string",
            "minLength": 1
          },
          {
            "type": "array",
            "items": {
              "type": "string",
              "minLength": 1
            }
          },
          {
            "type": "object",
            "properties": {
              "in": {
                "oneOf": [
                  {
                    "type": "string",
                    "minLength": 1
                  },
                  {
                    "type": "array",
                    "items": {
                      "type": "string",
                      "minLength": 1
                    },
                    "minItems": 1
                  }
                ]
              },
              "not_in": {
                "oneOf": [
                  {
                    "type": "string",
                    "minLength": 1
                  },
                  {
                    "type": "array",
                    "items": {
                      "type": "string",
                      "minLength": 1
                    },
                    "minItems": 1
                  }
                ]
              },
              "is_null": {
                "type": "boolean"
              },
              "under": {
                "type": "string",
                "minLength": 1
              }
            },
            "additionalProperties": false,
            "minProperties": 1,
            "maxProperties": 1
          }
        ]
      }
    },
    "people": {
      "type": "object",
      "properties": {
        "assignee": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "assignee_in_team": {
          "type": "string",
          "minLength": 1
        },
        "reporter": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "unassigned": {
          "type": "boolean"
        },
        "assignment_role": {
          "type": "object",
          "properties": {
            "role": {
              "type": "string",
              "minLength": 1
            },
            "person": {
              "type": "string",
              "minLength": 1
            }
          },
          "required": [
            "role",
            "person"
          ]
        }
      },
      "additionalProperties": false
    },
    "people2": {
      "type": "object",
      "properties": {
        "reports_to": {
          "type": "string",
          "minLength": 1
        },
        "kind": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        }
      },
      "required": [
        "reports_to"
      ],
      "additionalProperties": false
    },
    "priority": {
      "type": "object",
      "minProperties": 1,
      "properties": {
        "gte": {
          "enum": [
            "lowest",
            "low",
            "medium",
            "high",
            "highest",
            "critical"
          ]
        },
        "lte": {
          "enum": [
            "lowest",
            "low",
            "medium",
            "high",
            "highest",
            "critical"
          ]
        },
        "eq": {
          "enum": [
            "lowest",
            "low",
            "medium",
            "high",
            "highest",
            "critical"
          ]
        },
        "in": {
          "type": "array",
          "items": {
            "enum": [
              "lowest",
              "low",
              "medium",
              "high",
              "highest",
              "critical"
            ]
          }
        }
      },
      "additionalProperties": false
    },
    "weight": {
      "type": "object",
      "minProperties": 1,
      "properties": {
        "gte": {
          "type": "number"
        },
        "lte": {
          "type": "number"
        },
        "eq": {
          "type": "number"
        },
        "scheme": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": false
    },
    "impact": {
      "type": "object",
      "minProperties": 1,
      "properties": {
        "gte": {
          "type": "integer",
          "minimum": 1,
          "maximum": 5
        },
        "lte": {
          "type": "integer",
          "minimum": 1,
          "maximum": 5
        },
        "eq": {
          "type": "integer",
          "minimum": 1,
          "maximum": 5
        },
        "in": {
          "type": "array",
          "items": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5
          }
        }
      },
      "additionalProperties": false
    },
    "touched": {
      "type": "object",
      "properties": {
        "since": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        }
      },
      "required": [
        "since"
      ],
      "additionalProperties": false
    },
    "state": {
      "type": "object",
      "properties": {
        "category": {
          "type": "array",
          "items": {
            "enum": [
              "open",
              "active",
              "done"
            ]
          }
        },
        "in": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        }
      },
      "additionalProperties": false
    },
    "stage": {
      "type": "object",
      "properties": {
        "in": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "process": {
          "type": "string",
          "minLength": 1
        },
        "age_gt_expected": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "type": {
      "type": "object",
      "properties": {
        "in": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        }
      },
      "required": [
        "in"
      ],
      "additionalProperties": false
    },
    "tags": {
      "type": "object",
      "properties": {
        "all": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "any": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "none": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        }
      },
      "additionalProperties": false
    },
    "risk": {
      "type": "object",
      "properties": {
        "open_score_gte": {
          "type": "number"
        },
        "linked": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": false
    },
    "violations": {
      "type": "object",
      "properties": {
        "open": {
          "type": "boolean"
        },
        "category": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        },
        "severity_gte": {
          "enum": [
            "info",
            "warning",
            "violation",
            "critical"
          ]
        }
      },
      "additionalProperties": false
    },
    "text": {
      "type": "string",
      "minLength": 1
    },
    "project": {
      "type": "object",
      "properties": {
        "in": {
          "oneOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1
              },
              "minItems": 1
            }
          ]
        }
      },
      "required": [
        "in"
      ],
      "additionalProperties": false
    },
    "include_inbox": {
      "type": "boolean"
    },
    "due_date": {
      "type": "object",
      "minProperties": 1,
      "maxProperties": 1,
      "properties": {
        "between": {
          "type": "array",
          "items": {
            "type": "string",
            "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
          },
          "minItems": 2,
          "maxItems": 2
        },
        "before": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "after": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "is_null": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "planned_end": {
      "type": "object",
      "minProperties": 1,
      "maxProperties": 1,
      "properties": {
        "between": {
          "type": "array",
          "items": {
            "type": "string",
            "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
          },
          "minItems": 2,
          "maxItems": 2
        },
        "before": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "after": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "is_null": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "planned_start": {
      "type": "object",
      "minProperties": 1,
      "maxProperties": 1,
      "properties": {
        "between": {
          "type": "array",
          "items": {
            "type": "string",
            "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
          },
          "minItems": 2,
          "maxItems": 2
        },
        "before": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "after": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "is_null": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "actual_end": {
      "type": "object",
      "minProperties": 1,
      "maxProperties": 1,
      "properties": {
        "between": {
          "type": "array",
          "items": {
            "type": "string",
            "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
          },
          "minItems": 2,
          "maxItems": 2
        },
        "before": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "after": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "is_null": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "created": {
      "type": "object",
      "minProperties": 1,
      "maxProperties": 1,
      "properties": {
        "between": {
          "type": "array",
          "items": {
            "type": "string",
            "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
          },
          "minItems": 2,
          "maxItems": 2
        },
        "before": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "after": {
          "type": "string",
          "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d"
        },
        "is_null": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "and": {
      "type": "array",
      "items": {
        "$ref": "#"
      },
      "minItems": 1
    },
    "or": {
      "type": "array",
      "items": {
        "$ref": "#"
      },
      "minItems": 1
    },
    "not": {
      "$ref": "#"
    }
  },
  "additionalProperties": false
}
```
