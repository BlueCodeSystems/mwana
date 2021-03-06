# vim: ai ts=4 sts=4 et sw=4
from django.db import models
from rapidsms.models import Contact
from django.contrib.auth.models import User




class Issue(models.Model):

    TYPE_CHOICES = (
        ('feature', 'New Feature'),
        ('change', 'Change Request'),
        ('bug', 'Bug Fix'),
        ('meeting', 'Meeting'),
        ('other', 'Other'),)

    STATUS_CHOICES = (
        ('new', 'New'),
        ('ongoing', 'On-going'),
        ('completed', 'Completed'),
        ('bugfixed', 'Bug Fixed'),
        ('obsolete', 'Obsolete'),
        ('resurfaced', 'Resurfaced'),
        ('future', 'Future'),
        ('closed', 'Closed'),)

    PRIORITY_CHOICES = (
        ('high', 'High'),
        ('medium', 'Medium'),
        ('Low', 'Low'),)

    date_created = models.DateTimeField(auto_now_add=True, editable=False)    
    edited_on = models.DateTimeField(auto_now=True)
    type = models.CharField(choices=TYPE_CHOICES, max_length=15, blank=True)
    status = models.CharField(choices=STATUS_CHOICES, max_length=15, default='new')
    priority = models.CharField(choices=PRIORITY_CHOICES, max_length=7, default='medium')
    title = models.CharField(max_length=160)
    body = models.TextField()
    sms_author = models.ForeignKey(Contact, null=True, blank=True, editable=False) #author
    web_author = models.ForeignKey(User, null=True, blank=True, editable=False) # author
    assigned_to = models.ForeignKey(User, null=True, blank=True,
                                    related_name="assigned_to",
                                    limit_choices_to={'is_active':'True', 'is_staff':'True'})
    start_date = models.DateField(null=True, blank=True, verbose_name='Actual Start Date')
    end_date = models.DateField(null=True, blank=True, verbose_name='Actual End Date')
    desired_start_date = models.DateField(null=True, blank=True)
    desired_completion_date = models.DateField(null=True, blank=True,  verbose_name='Desired End Date')
    open = models.NullBooleanField(null=True, blank=True, editable=False)

    def __unicode__(self):
        return self.title


class Comment(models.Model):
    date_created = models.DateTimeField(auto_now_add=True)
    edited_on = models.DateTimeField(auto_now=True)
    body = models.TextField()
    issue = models.ForeignKey(Issue)

    def __unicode__(self):
        return "%s..." % self.body[0:50]
