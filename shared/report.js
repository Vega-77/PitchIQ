// The match report's logic, gathered from the report-*.js files beside it.
//
// Pages and the pure test suites import from here. The parts import nothing but
// each other: no DOM and no Firestore, which is deliberate. It keeps every
// decision about what a report says testable on its own, and lets Node load
// this file straight from disk. tests/test_report_parts.py holds that line.

export {
    FROM_WHISTLE, FROM_LAST_TAG, whistleFrom, minutesFrom, knownMinutes, minutesNote, onPitchAt,
    stintOverlapS, FIRST_HALF, HALF_TIME, SECOND_HALF, matchClockMap, clockMapNote,
    clockFromMatch, rankRosterForCluster, TRACKED_SHARE_FLOOR, windowClockRange, trackedCoverage,
    metresPerMinute, coverageNote,
} from './report-clock.js?v=150';

export {
    FROM_VIDEO, FROM_TAGGED, BY_CLOCK, BY_DOUBT, orderFeed, orderCaveat, GOAL_PAIR_S, reviewFeed,
    CONFIRMED_STATUS, REJECTED_STATUS, EDITED_STATUS, hasVerdict, currentAnswers, reviewScore,
    reviewLabels,
} from './report-review.js?v=150';

export {
    SHOT_RESULTS, shotLedger, headerCorrection, headerNote, correctedShotMarks, xgTally,
    sumXgTallies, xgCalibration, calibrationNote,
} from './report-shots.js?v=150';

export {
    MAX_TOUCH_TIMES, NOT_A_PLAYER, cvStatsByPlayer, cvReportFields, matchLine, playerTimeline,
} from './report-player.js?v=150';

export {
    SAME_KIT_CHROMA, kitDistance, sameFigureCandidates,
} from './report-same-person.js?v=150';

export {
    possessionIsInPlay, printStamp, roughDuration, shapeConfidence, xgTrust, periodNote,
    ACCEL_NOISE_CEILING_M, playerWobbleNote, blindSplit, cvQualityNotes,
} from './report-quality-notes.js?v=150';

export {
    railTarget,
} from './report-rail.js?v=150';

export {
    STAT_TYPES, groupStats,
} from './report-stats.js?v=150';

export {
    POSITIONS, positionOf, positionLabel, isKeeper, groupByPosition, MAX_BAND_M, positionBand,
    coverageVerdict, positionalPlay,
} from './report-positions.js?v=150';

export {
    FULL_MATCH_MIN, seasonGroups, formGuide, daysBetween, nextFixture, whenLabel, seasonJobs,
    seasonMoments,
} from './report-season.js?v=150';

export {
    SHARE, COUNT, RATE, comparePair, verdict, insideNoise, taggedCount, taggedTeamRows,
    teamStatRows, shapeStatRows, keeperStatRows, keeperOfTrack,
} from './report-teams.js?v=150';

export {
    syncState, safeToClose,
} from './report-sync.js?v=150';

export {
    pressingTrend, pressingRead, pressingNote, cvReads,
} from './report-press.js?v=150';

export {
    matchBrief,
} from './report-brief.js?v=150';

export {
    shareShifted, substitutionChanges, substitutionWindows, substitutionRead, CHANGE_REASONS,
    substitutionNote,
} from './report-substitutions.js?v=150';

export {
    mappingWithout, erasureNote,
} from './report-erasure.js?v=150';
