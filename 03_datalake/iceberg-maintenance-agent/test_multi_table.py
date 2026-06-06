#!/usr/bin/env python3
"""
Test multi-table monitoring and summary report generation.
"""

import sys
sys.path.insert(0, 'src')

from monitor.config import MonitorConfig, ConfigManager
from monitor.metrics import FileMetrics, SnapshotMetrics, OrphanMetrics, TableMetrics
from monitor.recommendations import RecommendationEngine
from monitor.reporter import Reporter
from datetime import datetime


def create_table_metrics(table_name, scenario):
    """Create metrics for different scenarios"""
    
    scenarios = {
        'critical': {
            'files': (500, 450, 25.0, 15.0, 12500.0),
            'snapshots': (200, 8760.0, 1.0, 120, 120.0),
            'orphans': (100, 5000.0)
        },
        'needs_compaction': {
            'files': (300, 250, 50.0, 40.0, 15000.0),
            'snapshots': (50, 100.0, 1.0, 0, 0.0),
            'orphans': (0, 0.0)
        },
        'needs_snapshots': {
            'files': (100, 10, 200.0, 180.0, 20000.0),
            'snapshots': (150, 5000.0, 1.0, 80, 80.0),
            'orphans': (0, 0.0)
        },
        'healthy': {
            'files': (50, 5, 256.0, 250.0, 12800.0),
            'snapshots': (50, 120.0, 1.0, 0, 0.0),
            'orphans': (0, 0.0)
        }
    }
    
    data = scenarios[scenario]
    
    file_metrics = FileMetrics(
        total_files=data['files'][0],
        small_files=data['files'][1],
        average_file_size_mb=data['files'][2],
        median_file_size_mb=data['files'][3],
        total_size_mb=data['files'][4],
        file_size_distribution={
            "0-50MB": data['files'][1],
            "50-100MB": 10,
            "100-200MB": 10,
            "200-500MB": 10,
            "500MB+": 5
        }
    )
    
    snapshot_metrics = SnapshotMetrics(
        total_snapshots=data['snapshots'][0],
        oldest_snapshot_age_hours=data['snapshots'][1],
        newest_snapshot_age_hours=data['snapshots'][2],
        snapshots_to_expire=data['snapshots'][3],
        estimated_metadata_reduction_mb=data['snapshots'][4]
    )
    
    orphan_metrics = OrphanMetrics(
        orphan_file_count=data['orphans'][0],
        orphan_total_size_mb=data['orphans'][1],
        orphan_file_paths=[]
    )
    
    return TableMetrics(
        table_location=f"s3://warehouse/db/{table_name}",
        snapshot_id=f"snap_{table_name}",
        analysis_timestamp=datetime.now().isoformat(),
        file_metrics=file_metrics,
        snapshot_metrics=snapshot_metrics,
        orphan_metrics=orphan_metrics
    )


def main():
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 22 + "MULTI-TABLE MONITORING TEST" + " " * 29 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    
    # Create configuration
    config = ConfigManager.get_default_config()
    config.tables = [
        's3://warehouse/db/events',
        's3://warehouse/db/users',
        's3://warehouse/db/transactions',
        's3://warehouse/db/analytics'
    ]
    
    # Create recommendation engine and reporter
    engine = RecommendationEngine(config)
    reporter = Reporter()
    
    # Simulate monitoring multiple tables
    print("=" * 80)
    print("MONITORING 4 TABLES")
    print("=" * 80)
    
    tables = [
        ('events', 'critical'),
        ('users', 'needs_compaction'),
        ('transactions', 'needs_snapshots'),
        ('analytics', 'healthy')
    ]
    
    reports = []
    
    for table_name, scenario in tables:
        print(f"\nAnalyzing table: {table_name} ({scenario})")
        
        # Create metrics
        metrics = create_table_metrics(table_name, scenario)
        
        # Generate recommendations
        recommendations = engine.generate_recommendations(metrics)
        
        # Create report
        report = reporter.generate_report(metrics, recommendations)
        reports.append(report)
        
        print(f"  Files: {metrics.file_metrics.total_files}, "
              f"Snapshots: {metrics.snapshot_metrics.total_snapshots}, "
              f"Orphans: {metrics.orphan_metrics.orphan_file_count}")
        print(f"  Recommendations: {len(recommendations)}")
        
        if recommendations:
            for rec in recommendations:
                print(f"    - {rec.recommendation_type.value} [{rec.urgency.value}]")
    
    # Generate summary report
    print("\n" + "=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)
    
    summary = reporter.generate_summary_report(reports)
    print(summary)
    
    # Show individual reports for tables needing maintenance
    print("\n" + "=" * 80)
    print("DETAILED REPORTS FOR TABLES NEEDING MAINTENANCE")
    print("=" * 80)
    
    for report in reports:
        if report.recommendations:
            print("\n" + "─" * 80)
            print(report.to_text())
    
    print("\n" + "=" * 80)
    print("✓ Multi-table monitoring test complete!")
    print("=" * 80)
    print()


if __name__ == '__main__':
    main()
