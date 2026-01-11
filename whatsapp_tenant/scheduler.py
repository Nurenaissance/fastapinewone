"""
Smart Group Auto-Sync Scheduler
Automatically syncs smart groups with matching contacts on a schedule
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from config.database import SessionLocal
from .models import BroadcastGroups
from .group_service import GroupService
from tenant.models import Tenant
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

class SmartGroupScheduler:
    """Manages scheduled auto-sync for smart groups"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False

    def sync_all_smart_groups(self) -> Dict:
        """
        Sync all smart groups across all tenants

        Returns:
            dict with sync statistics
        """
        start_time = datetime.now()
        logger.info("=" * 80)
        logger.info(f"SMART GROUP AUTO-SYNC STARTED at {start_time}")
        logger.info("=" * 80)

        db: Session = SessionLocal()
        stats = {
            'total_groups_processed': 0,
            'total_groups_synced': 0,
            'total_contacts_added': 0,
            'total_contacts_removed': 0,
            'errors': 0,
            'tenants_processed': set(),
            'sync_details': []
        }

        try:
            # Get all groups with auto_rules enabled
            smart_groups = db.query(BroadcastGroups).filter(
                BroadcastGroups.auto_rules.isnot(None)
            ).all()

            logger.info(f"Found {len(smart_groups)} groups with auto_rules")

            for group in smart_groups:
                stats['total_groups_processed'] += 1
                stats['tenants_processed'].add(group.tenant_id)

                try:
                    # Check if auto_rules are enabled
                    if not group.auto_rules or not group.auto_rules.get('enabled'):
                        logger.debug(f"Group {group.id} ({group.name}): auto_rules disabled, skipping")
                        continue

                    logger.info(f"Syncing group: {group.name} (ID: {group.id}, Tenant: {group.tenant_id})")

                    # Sync the group
                    result = GroupService.sync_group_members(group, db)

                    if result['synced']:
                        stats['total_groups_synced'] += 1
                        stats['total_contacts_added'] += result.get('members_added', 0)
                        stats['total_contacts_removed'] += result.get('members_removed', 0)

                        stats['sync_details'].append({
                            'group_id': group.id,
                            'group_name': group.name,
                            'tenant_id': group.tenant_id,
                            'members_before': result.get('members_before', 0),
                            'members_after': result.get('members_after', 0),
                            'members_added': result.get('members_added', 0),
                            'members_removed': result.get('members_removed', 0),
                            'status': 'success'
                        })

                        logger.info(
                            f"  ✅ Synced: {result['members_before']} -> {result['members_after']} members "
                            f"(+{result['members_added']}, -{result['members_removed']})"
                        )
                    else:
                        logger.warning(f"  ⚠️ Sync skipped: {result.get('reason', 'Unknown reason')}")

                except Exception as e:
                    stats['errors'] += 1
                    logger.error(f"  ❌ Error syncing group {group.id} ({group.name}): {str(e)}")
                    stats['sync_details'].append({
                        'group_id': group.id,
                        'group_name': group.name,
                        'tenant_id': group.tenant_id,
                        'status': 'error',
                        'error': str(e)
                    })

            # Calculate duration
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            # Log summary
            logger.info("=" * 80)
            logger.info("SMART GROUP AUTO-SYNC COMPLETED")
            logger.info(f"Duration: {duration:.2f} seconds")
            logger.info(f"Tenants processed: {len(stats['tenants_processed'])}")
            logger.info(f"Groups processed: {stats['total_groups_processed']}")
            logger.info(f"Groups synced: {stats['total_groups_synced']}")
            logger.info(f"Contacts added: {stats['total_contacts_added']}")
            logger.info(f"Contacts removed: {stats['total_contacts_removed']}")
            logger.info(f"Errors: {stats['errors']}")
            logger.info("=" * 80)

            stats['duration_seconds'] = duration
            stats['start_time'] = start_time.isoformat()
            stats['end_time'] = end_time.isoformat()
            stats['tenants_processed'] = list(stats['tenants_processed'])

        except Exception as e:
            logger.error(f"CRITICAL ERROR in sync_all_smart_groups: {str(e)}", exc_info=True)
            stats['critical_error'] = str(e)

        finally:
            db.close()

        return stats

    def sync_tenant_smart_groups(self, tenant_id: str) -> Dict:
        """
        Sync smart groups for a specific tenant only

        Args:
            tenant_id: The tenant ID to sync groups for

        Returns:
            dict with sync statistics
        """
        logger.info(f"Starting smart group sync for tenant: {tenant_id}")

        db: Session = SessionLocal()
        stats = {
            'tenant_id': tenant_id,
            'groups_processed': 0,
            'groups_synced': 0,
            'contacts_added': 0,
            'contacts_removed': 0,
            'errors': 0,
            'sync_details': []
        }

        try:
            # Get smart groups for this tenant
            smart_groups = db.query(BroadcastGroups).filter(
                BroadcastGroups.tenant_id == tenant_id,
                BroadcastGroups.auto_rules.isnot(None)
            ).all()

            logger.info(f"Found {len(smart_groups)} smart groups for tenant {tenant_id}")

            for group in smart_groups:
                stats['groups_processed'] += 1

                try:
                    if not group.auto_rules or not group.auto_rules.get('enabled'):
                        continue

                    result = GroupService.sync_group_members(group, db)

                    if result['synced']:
                        stats['groups_synced'] += 1
                        stats['contacts_added'] += result.get('members_added', 0)
                        stats['contacts_removed'] += result.get('members_removed', 0)

                        stats['sync_details'].append({
                            'group_id': group.id,
                            'group_name': group.name,
                            'members_after': result.get('members_after', 0),
                            'members_added': result.get('members_added', 0),
                            'members_removed': result.get('members_removed', 0)
                        })

                except Exception as e:
                    stats['errors'] += 1
                    logger.error(f"Error syncing group {group.id}: {str(e)}")

            logger.info(f"Tenant {tenant_id} sync complete: {stats['groups_synced']} groups synced")

        except Exception as e:
            logger.error(f"Error syncing tenant {tenant_id}: {str(e)}")
            stats['error'] = str(e)

        finally:
            db.close()

        return stats

    def start(self, hour: int = 2, minute: int = 0):
        """
        Start the scheduler with daily sync at specified time

        Args:
            hour: Hour to run sync (0-23), default 2 AM
            minute: Minute to run sync (0-59), default 0
        """
        if self.is_running:
            logger.warning("Scheduler is already running")
            return

        # Add daily sync job
        self.scheduler.add_job(
            self.sync_all_smart_groups,
            trigger=CronTrigger(hour=hour, minute=minute),
            id='daily_smart_group_sync',
            name='Daily Smart Group Sync',
            replace_existing=True,
            max_instances=1  # Prevent overlapping executions
        )

        self.scheduler.start()
        self.is_running = True

        logger.info(f"✅ Smart Group Auto-Sync Scheduler STARTED")
        logger.info(f"   Daily sync scheduled for {hour:02d}:{minute:02d}")
        logger.info(f"   Next run: {self.scheduler.get_job('daily_smart_group_sync').next_run_time}")

    def start_with_interval(self, hours: int = 24):
        """
        Start scheduler with interval-based sync (alternative to cron)

        Args:
            hours: Number of hours between syncs
        """
        if self.is_running:
            logger.warning("Scheduler is already running")
            return

        self.scheduler.add_job(
            self.sync_all_smart_groups,
            trigger=IntervalTrigger(hours=hours),
            id='interval_smart_group_sync',
            name=f'Smart Group Sync (Every {hours}h)',
            replace_existing=True,
            max_instances=1,
            next_run_time=datetime.now()  # Run immediately on start
        )

        self.scheduler.start()
        self.is_running = True

        logger.info(f"✅ Smart Group Auto-Sync Scheduler STARTED (Interval Mode)")
        logger.info(f"   Sync interval: Every {hours} hours")
        logger.info(f"   Next run: {self.scheduler.get_job('interval_smart_group_sync').next_run_time}")

    def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            logger.warning("Scheduler is not running")
            return

        self.scheduler.shutdown()
        self.is_running = False
        logger.info("Smart Group Auto-Sync Scheduler STOPPED")

    def get_status(self) -> Dict:
        """Get scheduler status and job information"""
        if not self.is_running:
            return {
                'running': False,
                'message': 'Scheduler is not running'
            }

        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })

        return {
            'running': True,
            'jobs': jobs,
            'scheduler_state': self.scheduler.state
        }

    def trigger_manual_sync(self) -> Dict:
        """
        Manually trigger a sync outside the schedule

        Returns:
            dict with sync results
        """
        logger.info("Manual smart group sync triggered")
        return self.sync_all_smart_groups()


# Global scheduler instance
smart_group_scheduler = SmartGroupScheduler()
