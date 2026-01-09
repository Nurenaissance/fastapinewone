"""
Rule evaluation engine for dynamic group membership
Evaluates contacts against auto_rules criteria
"""
from datetime import datetime
from typing import Dict, Any, List
from contacts.models import Contact
import logging

logger = logging.getLogger(__name__)

class RuleEvaluator:
    """Evaluates contacts against rule conditions"""

    @staticmethod
    def evaluate_contact(contact: Contact, rules: Dict[str, Any]) -> bool:
        """
        Evaluate if a contact matches the given rules

        Args:
            contact: Contact model instance
            rules: Dictionary containing auto_rules structure

        Returns:
            bool: True if contact matches all conditions (AND logic)
        """
        if not rules or not rules.get('enabled', False):
            return False

        conditions = rules.get('conditions', [])
        if not conditions:
            return False

        # AND logic: all conditions must match
        for condition in conditions:
            if not RuleEvaluator._evaluate_condition(contact, condition):
                return False

        return True

    @staticmethod
    def _evaluate_condition(contact: Contact, condition: Dict[str, Any]) -> bool:
        """Evaluate a single condition against a contact"""
        try:
            condition_type = condition.get('type')
            field = condition.get('field')
            operator = condition.get('operator')
            expected_value = condition.get('value')

            # Get actual value from contact
            actual_value = RuleEvaluator._get_contact_field_value(contact, field, condition_type)

            logger.debug(f"Evaluating contact {contact.id}: field={field}, actual={actual_value}, operator={operator}, expected={expected_value}")

            # Handle None values
            if actual_value is None:
                logger.debug(f"Contact {contact.id}: field {field} is None, returning False")
                return operator in ['not_equals']  # Only not_equals can match None

            # Apply operator
            result = RuleEvaluator._apply_operator(actual_value, operator, expected_value, condition_type)
            logger.debug(f"Contact {contact.id}: condition result = {result}")
            return result
        except Exception as e:
            logger.warning(f"Condition evaluation failed for contact {contact.id}: {condition} - {e}")
            return False

    @staticmethod
    def _get_contact_field_value(contact: Contact, field: str, condition_type: str) -> Any:
        """Extract field value from contact, supporting nested JSON fields"""

        # Handle custom field paths (e.g., "customField.status")
        if condition_type == 'custom_field' and '.' in field:
            parts = field.split('.')
            if parts[0] == 'customField':
                custom_data = contact.customField
                if not custom_data:
                    return None

                # Navigate nested structure
                value = custom_data
                for key in parts[1:]:
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        return None
                return value

        # Handle standard fields
        return getattr(contact, field, None)

    @staticmethod
    def _apply_operator(actual: Any, operator: str, expected: Any, condition_type: str) -> bool:
        """Apply comparison operator"""

        try:
            # Text operators
            if operator == 'equals':
                return str(actual).lower() == str(expected).lower()

            elif operator == 'not_equals':
                return str(actual).lower() != str(expected).lower()

            elif operator == 'contains':
                return str(expected).lower() in str(actual).lower()

            elif operator == 'starts_with':
                return str(actual).lower().startswith(str(expected).lower())

            elif operator == 'ends_with':
                return str(actual).lower().endswith(str(expected).lower())

            # Date/Numeric operators
            elif operator == 'greater_than':
                if condition_type in ['date', 'engagement']:
                    try:
                        actual_dt = RuleEvaluator._parse_datetime(actual)
                        expected_dt = RuleEvaluator._parse_datetime(expected)
                        logger.debug(f"Date comparison: {actual_dt} > {expected_dt} = {actual_dt > expected_dt}")
                        return actual_dt > expected_dt
                    except Exception as e:
                        logger.error(f"Failed to parse datetime for greater_than: actual={actual}, expected={expected}, error={e}")
                        return False
                return float(actual) > float(expected)

            elif operator == 'less_than':
                if condition_type in ['date', 'engagement']:
                    try:
                        actual_dt = RuleEvaluator._parse_datetime(actual)
                        expected_dt = RuleEvaluator._parse_datetime(expected)
                        logger.debug(f"Date comparison: {actual_dt} < {expected_dt} = {actual_dt < expected_dt}")
                        return actual_dt < expected_dt
                    except Exception as e:
                        logger.error(f"Failed to parse datetime for less_than: actual={actual}, expected={expected}, error={e}")
                        return False
                return float(actual) < float(expected)

            elif operator == 'in_range':
                if condition_type in ['date', 'engagement']:
                    actual_dt = RuleEvaluator._parse_datetime(actual)

                    # Handle dict or DateRangeValue object
                    if isinstance(expected, dict):
                        start = expected.get('start')
                        end = expected.get('end')
                    else:
                        # Assume it's an object with start/end attributes
                        start = getattr(expected, 'start', None)
                        end = getattr(expected, 'end', None)

                    start_dt = RuleEvaluator._parse_datetime(start) if start else None
                    end_dt = RuleEvaluator._parse_datetime(end) if end else None

                    if start_dt and end_dt:
                        return start_dt <= actual_dt <= end_dt
                    elif start_dt:
                        return actual_dt >= start_dt
                    elif end_dt:
                        return actual_dt <= end_dt

                return False

            return False

        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"Operator evaluation failed: {operator} - {e}")
            return False

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        """Parse datetime from various formats"""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            # Try ISO format
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                # Try other common formats
                try:
                    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return datetime.strptime(value, '%Y-%m-%d')
        raise ValueError(f"Cannot parse datetime from {type(value)}")

    @staticmethod
    def get_matching_contacts(db_session, tenant_id: str, rules: Dict[str, Any]) -> List[Contact]:
        """
        Get all contacts that match the given rules

        Args:
            db_session: SQLAlchemy session
            tenant_id: Tenant ID to filter contacts
            rules: Auto rules dictionary

        Returns:
            List of matching Contact objects
        """
        logger.info(f"Fetching contacts for tenant_id={tenant_id}")
        logger.info(f"Rules: {rules}")

        # Get all contacts for tenant
        all_contacts = db_session.query(Contact).filter(
            Contact.tenant_id == tenant_id
        ).all()

        logger.info(f"Total contacts found: {len(all_contacts)}")

        # Log sample of contacts with their createdOn values
        if all_contacts:
            sample_size = min(5, len(all_contacts))
            logger.info(f"Sample contacts (first {sample_size}):")
            for contact in all_contacts[:sample_size]:
                logger.info(f"  Contact {contact.id}: phone={contact.phone}, createdOn={contact.createdOn}")

        # Filter using rule evaluation
        matching_contacts = [
            contact for contact in all_contacts
            if RuleEvaluator.evaluate_contact(contact, rules)
        ]

        logger.info(f"Matching contacts: {len(matching_contacts)}")
        return matching_contacts
