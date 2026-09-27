"""Outbound notifications (Slack). Callers never see an exception from here."""

from harvey.notify.slack import SlackNotifier

__all__ = ["SlackNotifier"]
