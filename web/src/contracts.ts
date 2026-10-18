/* Generated from contracts/ by npm run gen:contracts. Do not edit. */

export interface TouchlineContracts {
  Incident?: Incident;
  MatchEvent?: MatchEvent;
  OverlayCue?: OverlayCue;
  ReplayPackage?: ReplayPackage;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "Incident".
 */
export interface Incident {
  decided_at?: number | null;
  facts?: {
    [k: string]: number | string | boolean;
  };
  final?: string | null;
  goal_event?: string | null;
  incident_id: string;
  kind: "offside" | "foul" | "penalty" | "handball" | "red_card" | "goal_check";
  on_field: string;
  opened_at: number;
  reason_codes?: (
    "tight_margin" | "subjective_interference" | "obstructed_view" | "multiple_phases" | "earlier_offence"
  )[];
  state: "open" | "reviewing" | "decided" | "restarted";
  var_outcome?: string | null;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "MatchEvent".
 */
export interface MatchEvent {
  ball_speed_kmh?: number | null;
  ball_track?: [number, number, number][] | null;
  clock: number;
  end?: [number, number] | null;
  event_id: string;
  frame?: BodyFrame | null;
  match_id: string;
  outcome?: string | null;
  period: 1 | 2;
  player?: string | null;
  positions: {
    /**
     * @minItems 2
     * @maxItems 2
     *
     * This interface was referenced by `undefined`'s JSON-Schema definition
     * via the `patternProperty` "^[A-Z]{3}-\d{1,2}$".
     */
    [k: string]: [number, number];
  };
  receiver?: string | null;
  start?: [number, number] | null;
  team?: string | null;
  type:
    | "kickoff"
    | "pass"
    | "carry"
    | "shot"
    | "tackle"
    | "interception"
    | "pressure"
    | "foul"
    | "card"
    | "goal"
    | "var_check"
    | "var_decision"
    | "period_end";
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "BodyFrame".
 */
export interface BodyFrame {
  body: {
    /**
     * This interface was referenced by `undefined`'s JSON-Schema definition
     * via the `patternProperty` "^[A-Z]{3}-\d{1,2}$".
     */
    [k: string]: {
      /**
       * @minItems 2
       * @maxItems 2
       */
      [k: string]: [number, number];
    };
  };
  clock: number;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "OverlayCue".
 */
export interface OverlayCue {
  approval: "auto" | "held" | "approved" | "killed";
  at: number;
  audience?: AudienceProfile | null;
  band?: Band | null;
  cue_id: string;
  graphics?: {
    [k: string]: {
      [k: string]: number | string;
    };
  };
  kind: "speed" | "pass_quality" | "pressure" | "momentum" | "goal" | "card" | "var_check" | "var_decision" | "recap";
  /**
   * @minItems 1
   */
  layers: [
    "scorebug" | "player_tag" | "chip" | "momentum" | "var_badge" | "offside_lines" | "band" | "recap",
    ...("scorebug" | "player_tag" | "chip" | "momentum" | "var_badge" | "offside_lines" | "band" | "recap")[]
  ];
  origin: "agent" | "template";
  /**
   * @minItems 1
   */
  sources: [string, ...string[]];
  ttl_s: number;
  validated: boolean;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "AudienceProfile".
 */
export interface AudienceProfile {
  club?: string | null;
  focus?: string | null;
  lang?: "en" | "es";
  persona?: "casual" | "analyst" | "ref_nerd";
  pin?: ("sprint_speed" | "distance" | "pass_difficulty" | "possession" | "pressure") | null;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "Band".
 */
export interface Band {
  tag: string;
  text: string;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "ReplayPackage".
 */
export interface ReplayPackage {
  /**
   * @minItems 2
   */
  ball: [BallSample, BallSample, ...BallSample[]];
  /**
   * @minItems 2
   */
  frames: [ReplayFrame, ReplayFrame, ...ReplayFrame[]];
  incident_id: string;
  moments: ReplayMoment[];
  slow?: SlowWindow | null;
  status: {
    [k: string]: string;
  };
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "BallSample".
 */
export interface BallSample {
  t: number;
  x: number;
  y: number;
  z?: number;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "ReplayFrame".
 */
export interface ReplayFrame {
  positions: {
    /**
     * @minItems 2
     * @maxItems 2
     *
     * This interface was referenced by `undefined`'s JSON-Schema definition
     * via the `patternProperty` "^[A-Z]{3}-\d{1,2}$".
     */
    [k: string]: [number, number];
  };
  t: number;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "ReplayMoment".
 */
export interface ReplayMoment {
  label: string;
  reveal: "always" | "during" | "after";
  t: number;
}
/**
 * This interface was referenced by `TouchlineContracts`'s JSON-Schema
 * via the `definition` "SlowWindow".
 */
export interface SlowWindow {
  end: number;
  rate: number;
  start: number;
}
