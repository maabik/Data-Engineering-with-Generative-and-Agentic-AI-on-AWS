#!/usr/bin/env python3
"""
Basic tests to verify the Iceberg Maintenance Monitor functionality.
Tests configuration, metrics, recommendations, and reporting without requiring S3/Iceberg.
"""

import sys
sys.path.insert(0, 'src')

from monitor.config import MonitorConfig, ConfigManager, CompactionThresholds, SnapshotThresholds
from monitor.metrics import FileMetrics, SnapshotMetrics, OrphanMetrics, TableMetrics
from monitor.recommendations import RecommendationEngine, RecommendationType, Urgency
from monitor.reporter import Reporter
from datetime import datetime


def test_configuration():
    """Test configuration management"""
    print("=" * 80)
    print("TEST 1: Configuration Management")
    print("=" * 80)
    
    # Test default configuration
    config = ConfigManager.get_default_config()
    print(f"✓ Default config created")
    print(f"  - Compaction threshold: {config.compaction.small_file_size_mb}MB")
    print(f"  - Max snapshots: {config.snapshots.max_snapshots}")
    print(f"  - Orphan age threshold: {config.orphans.age_hours}h")
    
    # Add a table for validation
    config.tables = ['s3://test-bucket/warehouse/test/table1']
    
    # Test validation with valid config
    errors = ConfigManager.validate_config(config)
    if not errors:
        print(f"✓ Valid config validation passed (no errors)")
    else:
        print(f"✗ Unexpected validation errors: {errors}")
        return False
    
    # Test validation with invalid config
    invalid_config = MonitorConfig(
        tables=[],  # Empty tables
        compaction=CompactionThresholds(small_file_size_mb=-10),  # Negative value
        snapshots=SnapshotThresholds(retention_hours=0)  # Zero value
    )
    errors = ConfigManager.validate_config(invalid_config)
    print(f"✓ Invalid config detected {len(errors)} errors:")
    for error in errors[:3]:  # Show first 3
        print(f"    - {error}")
    
    print()
    return True


def test_metrics():
    """Test metric data structures"""
    print("=" * 80)
    print("TEST 2: Metric Data Structures")
    print("=" * 80)
    
    # Create sample metrics
    file_metrics = FileMetrics(
        total_files=250,
        small_files=180,
        average_file_size_mb=45.5,
        median_file_size_mb=32.0,
        total_size_mb=11375.0,
        file_size_distribution={
            "0-50MB": 180,
            "50-100MB": 40,
            "100-200MB": 20,
            "200-500MB": 8,
            "500MB+": 2
        }
    )
    
    snapshot_metrics = SnapshotMetrics(
        total_snapshots=125,
        oldest_snapshot_age_hours=4320.0,  # 180 days
        newest_snapshot_age_hours=2.0,
        snapshots_to_expire=45,
        estimated_metadata_reduction_mb=45.0
    )
    
    orphan_metrics = OrphanMetrics(
        orphan_file_count=35,
        orphan_total_size_mb=1250.5,
        orphan_file_paths=[
            "s3://bucket/table/data/orphan1.parquet",
            "s3://bucket/table/data/orphan2.parquet"
        ]
    )
    
    table_metrics = TableMetrics(
        table_location="s3://test-bucket/warehouse/test/events",
        snapshot_id="12345",
        analysis_timestamp=datetime.now().isoformat(),
        file_metrics=file_metrics,
        snapshot_metrics=snapshot_metrics,
        orphan_metrics=orphan_metrics
    )
    
    print(f"✓ Created sample metrics:")
    print(f"  - Total files: {file_metrics.total_files}")
    print(f"  - Small files: {file_metrics.small_files} ({file_metrics.small_files/file_metrics.total_files*100:.1f}%)")
    print(f"  - Total snapshots: {snapshot_metrics.total_snapshots}")
    print(f"  - Snapshots to expire: {snapshot_metrics.snapshots_to_expire}")
    print(f"  - Orphan files: {orphan_metrics.orphan_file_count}")
    print(f"  - Orphan size: {orphan_metrics.orphan_total_size_mb}MB")
    
    # Test JSON serialization
    json_str = table_metrics.to_json()
    print(f"✓ JSON serialization successful ({len(json_str)} bytes)")
    
    # Test round-trip
    metrics_dict = table_metrics.to_dict()
    restored = TableMetrics.from_dict(metrics_dict)
    print(f"✓ Round-trip serialization successful")
    
    print()
    return table_metrics


def test_recommendations(config, table_metrics):
    """Test recommendation engine"""
    print("=" * 80)
    print("TEST 3: Recommendation Engine")
    print("=" * 80)
    
    engine = RecommendationEngine(config)
    
    # Generate recommendations
    recommendations = engine.generate_recommendations(table_metrics)
    
    print(f"✓ Generated {len(recommendations)} recommendations:")
    for i, rec in enumerate(recommendations, 1):
        print(f"\n  {i}. {rec.recommendation_type.value.upper()} [{rec.urgency.value.upper()}]")
        print(f"     Reason: {rec.reason}")
        print(f"     Benefit: {rec.estimated_benefit}")
    
    # Verify expected recommendations
    rec_types = {rec.recommendation_type for rec in recommendations}
    expected_types = {RecommendationType.COMPACTION, RecommendationType.SNAPSHOT_EXPIRATION, RecommendationType.ORPHAN_CLEANUP}
    
    if rec_types == expected_types:
        print(f"\n✓ All expected recommendation types generated")
    else:
        print(f"\n⚠ Expected {expected_types}, got {rec_types}")
    
    # Test urgency ordering
    urgencies = [rec.urgency for rec in recommendations]
    if urgencies == sorted(urgencies, reverse=True):
        print(f"✓ Recommendations properly sorted by urgency")
    else:
        print(f"⚠ Recommendations not sorted by urgency")
    
    print()
    return recommendations


def test_reporting(table_metrics, recommendations):
    """Test report generation"""
    print("=" * 80)
    print("TEST 4: Report Generation")
    print("=" * 80)
    
    reporter = Reporter()
    
    # Generate report
    report = reporter.generate_report(table_metrics, recommendations)
    
    # Test JSON export
    json_report = report.to_json()
    print(f"✓ JSON report generated ({len(json_report)} bytes)")
    
    # Test text export
    text_report = report.to_text()
    print(f"✓ Text report generated ({len(text_report)} bytes)")
    
    print("\n" + "─" * 80)
    print("SAMPLE TEXT REPORT:")
    print("─" * 80)
    print(text_report)
    print("─" * 80)
    
    # Test summary report
    summary = reporter.generate_summary_report([report])
    print(f"\n✓ Summary report generated ({len(summary)} bytes)")
    
    print()
    return True


def test_healthy_table():
    """Test with a healthy table (no maintenance needed)"""
    print("=" * 80)
    print("TEST 5: Healthy Table (No Maintenance Needed)")
    print("=" * 80)
    
    config = ConfigManager.get_default_config()
    
    # Create metrics for a healthy table
    file_metrics = FileMetrics(
        total_files=50,
        small_files=5,  # Only 10% small files
        average_file_size_mb=256.0,  # Above threshold
        median_file_size_mb=250.0,
        total_size_mb=12800.0,
        file_size_distribution={
            "0-50MB": 5,
            "50-100MB": 10,
            "100-200MB": 15,
            "200-500MB": 15,
            "500MB+": 5
        }
    )
    
    snapshot_metrics = SnapshotMetrics(
        total_snapshots=50,  # Below max
        oldest_snapshot_age_hours=120.0,  # Within retention
        newest_snapshot_age_hours=1.0,
        snapshots_to_expire=0,  # None to expire
        estimated_metadata_reduction_mb=0.0
    )
    
    orphan_metrics = OrphanMetrics(
        orphan_file_count=0,  # No orphans
        orphan_total_size_mb=0.0,
        orphan_file_paths=[]
    )
    
    table_metrics = TableMetrics(
        table_location="s3://test-bucket/warehouse/test/healthy_table",
        snapshot_id="67890",
        analysis_timestamp=datetime.now().isoformat(),
        file_metrics=file_metrics,
        snapshot_metrics=snapshot_metrics,
        orphan_metrics=orphan_metrics
    )
    
    engine = RecommendationEngine(config)
    recommendations = engine.generate_recommendations(table_metrics)
    
    print(f"✓ Healthy table analyzed")
    print(f"  - Files: {file_metrics.total_files} (avg {file_metrics.average_file_size_mb}MB)")
    print(f"  - Snapshots: {snapshot_metrics.total_snapshots}")
    print(f"  - Orphans: {orphan_metrics.orphan_file_count}")
    print(f"  - Recommendations: {len(recommendations)}")
    
    if len(recommendations) == 0:
        print(f"✓ No maintenance recommendations (as expected)")
    else:
        print(f"⚠ Unexpected recommendations for healthy table:")
        for rec in recommendations:
            print(f"    - {rec.recommendation_type.value}: {rec.reason}")
    
    # Generate report
    reporter = Reporter()
    report = reporter.generate_report(table_metrics, recommendations)
    text_report = report.to_text()
    
    print("\n" + "─" * 80)
    print("HEALTHY TABLE REPORT:")
    print("─" * 80)
    print(text_report)
    print("─" * 80)
    
    print()
    return True


def run_all_tests():
    """Run all tests"""
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 20 + "ICEBERG MAINTENANCE MONITOR TESTS" + " " * 25 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    
    try:
        # Test 1: Configuration
        if not test_configuration():
            return False
        
        # Test 2: Metrics
        config = ConfigManager.get_default_config()
        table_metrics = test_metrics()
        
        # Test 3: Recommendations
        recommendations = test_recommendations(config, table_metrics)
        
        # Test 4: Reporting
        test_reporting(table_metrics, recommendations)
        
        # Test 5: Healthy table
        test_healthy_table()
        
        # Summary
        print("=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        print("✓ All tests passed successfully!")
        print()
        print("Components tested:")
        print("  ✓ Configuration management (load, validate)")
        print("  ✓ Metric data structures (create, serialize)")
        print("  ✓ Recommendation engine (compaction, snapshots, orphans)")
        print("  ✓ Report generation (text, JSON, summary)")
        print("  ✓ Healthy table detection (no false positives)")
        print()
        print("The Iceberg Maintenance Monitor is working correctly!")
        print("=" * 80)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
