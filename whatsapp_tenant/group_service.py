"""
Service layer for broadcast group management with auto-rules
"""
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from .models import BroadcastGroups
from contacts.models import Contact
from .rule_engine import RuleEvaluator
import logging

logger = logging.getLogger(__name__)

class GroupService:
    """Business logic for managing broadcast groups"""

    @staticmethod
    def sync_group_members(group: BroadcastGroups, db: Session) -> Dict:
        """
        Synchronize group members based on auto_rules

        Args:
            group: BroadcastGroups instance
            db: Database session

        Returns:
            dict with sync statistics
        """
        if not group.auto_rules or not group.auto_rules.get('enabled'):
            return {
                'synced': False,
                'reason': 'Auto-rules not enabled',
                'members_added': 0,
                'members_removed': 0
            }

        # Get matching contacts
        matching_contacts = RuleEvaluator.get_matching_contacts(
            db,
            group.tenant_id,
            group.auto_rules
        )

        # Filter out contacts with manual_mode enabled
        # TODO: Uncomment when manual_mode column is added to database
        # matching_contacts = [c for c in matching_contacts if not c.manual_mode]

        # Build new members list
        new_members = [
            {
                'phone': str(contact.phone),
                'name': contact.name or str(contact.phone)
            }
            for contact in matching_contacts
        ]

        # Calculate changes
        old_count = len(group.members or [])
        new_count = len(new_members)

        # Update members
        group.members = new_members
        db.commit()

        logger.info(f"Group {group.id} synced: {old_count} -> {new_count} members")

        return {
            'synced': True,
            'members_before': old_count,
            'members_after': new_count,
            'members_added': max(0, new_count - old_count),
            'members_removed': max(0, old_count - new_count)
        }

    @staticmethod
    def auto_assign_contact_to_groups(contact: Contact, db: Session) -> List[str]:
        """
        Auto-assign a contact to all matching groups
        Called after contact creation/update

        Args:
            contact: Contact instance
            db: Database session

        Returns:
            List of group IDs the contact was added to
        """
        # Skip if contact has manual mode enabled
        # TODO: Uncomment when manual_mode column is added to database
        # if contact.manual_mode:
        #     logger.info(f"Contact {contact.phone} has manual_mode enabled, skipping auto-assignment")
        #     return []

        # Get all groups with auto-rules for this tenant
        groups_with_rules = db.query(BroadcastGroups).filter(
            BroadcastGroups.tenant_id == contact.tenant_id,
            BroadcastGroups.auto_rules.isnot(None)
        ).all()

        assigned_groups = []

        for group in groups_with_rules:
            if not group.auto_rules or not group.auto_rules.get('enabled'):
                continue

            # Check if contact matches rules
            if RuleEvaluator.evaluate_contact(contact, group.auto_rules):
                # Check if already a member
                existing_phones = {str(m.get('phone')) for m in (group.members or [])}

                if str(contact.phone) not in existing_phones:
                    # Add to group
                    if group.members is None:
                        group.members = []

                    group.members.append({
                        'phone': str(contact.phone),
                        'name': contact.name or str(contact.phone)
                    })

                    assigned_groups.append(group.id)
                    logger.info(f"Contact {contact.phone} auto-assigned to group {group.id}")

        if assigned_groups:
            db.commit()

        return assigned_groups

    @staticmethod
    def apply_rules_retroactively(group: BroadcastGroups, db: Session) -> Dict:
        """
        Apply rules to all existing contacts when rules are first created or updated
        This is an alias for sync_group_members for clarity

        Args:
            group: BroadcastGroups instance
            db: Database session

        Returns:
            dict with sync statistics
        """
        return GroupService.sync_group_members(group, db)
