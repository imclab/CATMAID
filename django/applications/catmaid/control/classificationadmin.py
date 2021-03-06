from collections import defaultdict
from itertools import combinations

from django import forms
from django.conf import settings
from django.contrib.formtools.wizard.views import SessionWizardView
from django.shortcuts import render_to_response

from catmaid.control.classification import get_classification_links_qs
from catmaid.control.classification import link_existing_classification
from catmaid.models import ClassInstance, Project

TEMPLATES = {"settings": "catmaid/classification/admin_settings.html",
             "taggroups": "catmaid/classification/admin_setup_tag_groups.html",
             "confirmation": "catmaid/classification/admin_confirmation.html"}

class SettingsForm(forms.Form):
    add_supersets = forms.BooleanField(required=False, initial=True,
        help_text="This field indicates if a tag set should include only " \
            "strictly projects of an actually available tag set. Or if " \
            "projects of super sets should get added. Super sets are tag " \
            "sets that include the tag set under consideration.")
    respect_superset_graphs = forms.BooleanField(required=False,
        help_text="If projects of super sets are added, this setting indicates " \
            "if classification graphs linked to those projects should actually " \
            "be considered missing if they are not linked in non-super set " \
            "projects. When not checked, only classification graphs linked to " \
            "from non-super set projects can be considered missing (also by " \
            "super set projects).")

class TagGroupSelectionForm(forms.Form):
    tag_groups = forms.MultipleChoiceField(required=False,
        widget=forms.CheckboxSelectMultiple())

class ConfirmationForm(forms.Form):
    pass

def get_tag_sets(add_supersets=False, prefetch=True):
    tag_sets=defaultdict(set)
    tag_supersets=defaultdict(set)
    project_ids = []

    # Build up data structure that maps tag sets to
    # projects. These projects include projects with
    # tag supersets.
    #
    # The query retrieval can be made much faster
    # when the package django-batch-select is used:
    # for p in Project.objects.batch_select('tags'):
    #     tags = p.tags_all
    #     ...
    # However, this new dependency isn't justified if
    # only used here (in an admin tool).
    for p in Project.objects.all():
        project_ids.append(p.id)
        tags = p.tags.all()
        if len(tags) == 0:
            continue
        tag_sets[frozenset(tags)].add(p)

    if add_supersets:
        for a, b in combinations(tag_sets, 2):
            if a < b:
                tag_sets[a].update(tag_sets[b])
                tag_supersets[a].update(tag_sets[b])
            elif b < a:
                tag_sets[b].update(tag_sets[a])
                tag_supersets[b].update(tag_sets[a])

    return project_ids, tag_sets, tag_supersets

def generate_tag_groups(add_supersets=True, respect_superset_graphs=False):
    """ This creates a tag sets dictionary. It ignores projects without any
    tags.
    """
    project_ids, tag_sets, tag_supersets = get_tag_sets(add_supersets)

    # Get a query set that retrieves all CiCi links for all project ids at once
    workspace = settings.ONTOLOGY_DUMMY_PROJECT_ID
    links_qs = get_classification_links_qs(workspace, project_ids)
    # Make sure the the project ids and the classification root ids are
    # prefetched
    links_qs = links_qs.select_related('class_instance_a__project__id',
        'class_instance_b__id')
    # Execute the query set to build a look up table
    projects_to_cls_links = {}
    for cici in links_qs:
        if cici.class_instance_a.project.id not in projects_to_cls_links:
            projects_to_cls_links[cici.class_instance_a.project.id] = set()
        projects_to_cls_links[cici.class_instance_a.project.id].add(
            cici.class_instance_b.id)

    # Test which groups of projects belonging to a particular tag group,
    # have non-uniform classification graph links
    # TODO: Test for independent *and* dependent workspace
    available_tag_groups = {}
    for tags, projects in tag_sets.items():
        differs = False
        cg_roots = set()
        projects_cgroots = {}
        # Collect all classification roots in this tag group
        for p in projects:
            try:
                # Get set of CiCi links for current project
                croots = projects_to_cls_links[p.id]
            except KeyError:
                # Use an empty set if there are no CiCi links for the
                # current project.
                croots = set()

            # Remember roots for this projects
            projects_cgroots[p] = {
                'linked': croots,
                'missing': [],
                'workspace': workspace,
            }
            # Add classification graphs introduced by supersets to the expected
            # graphs in this tag set.
            if p not in tag_supersets[tags] or respect_superset_graphs:
                    cg_roots.update(croots)
        # Check if there are updates needed for some projects
        num_differing = 0
        meta = []
        for p in projects_cgroots:
            croots = projects_cgroots[p]['linked']
            diff = cg_roots - croots
            if len(diff) > 0:
                differs = True
                projects_cgroots[p]['missing'] = diff
                num_differing = num_differing + 1
                strdiff = ", ".join([str(cg) for cg in diff])
                meta.append("[PID: %s Missing: %s]" % (p.id, strdiff))
        # If there is a difference, offer this tag group
        # for selection.
        if differs:
            # Generate a string representation of the tags and use
            # it as index for a project classification.
            taglist = list(tags)
            taglist.sort(key=lambda x: x.id)
            name = ", ".join([t.name for t in taglist])
            # Fill data structure for later use
            available_tag_groups[name] = {
                'project_cgroots': projects_cgroots,
                'all_cgroots': cg_roots,
                'num_differing': num_differing,
                'meta': meta,
            }
    return available_tag_groups

class ClassificationAdminWizard(SessionWizardView):

    def get_template_names(self):
        return [TEMPLATES[self.steps.current]]

    def get_context_data(self, **kwargs):
        context = super(ClassificationAdminWizard, self).get_context_data(**kwargs)
        context['catmaid_url'] = settings.CATMAID_URL

        # The project links selection needs some extra context
        extra_context = {}
        if self.steps.current == "taggroups":
            extra_context['num_tag_groups'] = len(self.get_tag_group_list())
        if self.steps.current == "confirmation":
            # Get all selected tag groups
            extra_context['tag_groups'] = self.get_selected_tag_groups()
        context.update(extra_context)

        return context

    def get_tag_group_list(self, add_supersets=True, respect_superset_graphs=False):
        """ Returns a list of tuples that represent the currently
        available tag groups.
        """
        # Set up the tag groups only once
        #if not self.request.session.get('available_tag_groups'):
        available_tag_groups = generate_tag_groups(add_supersets, respect_superset_graphs)
        self.request.session['available_tag_groups'] = available_tag_groups
        # Create the tag group tuple list
        tag_group_list = []
        tag_groups = self.request.session.get('available_tag_groups')
        for eg, group in tag_groups.items():
            name = eg + " (" + str(group['num_differing']) + "/" + \
                str(len(group['project_cgroots'])) + " differ: " + \
                ", ".join(group['meta']) + ")"
            tag_group_list.append( (eg, name) )

        return tag_group_list

    def get_form(self, step=None, data=None, files=None):
        form = super(ClassificationAdminWizard, self).get_form(step, data, files)
        # Determine step if not given
        if step is None:
            step = self.steps.current
        if step == "taggroups":
            # Update the tag groups list and select all by default
            add_supersets = self.get_cleaned_data_for_step('settings')['add_supersets']
            respect_superset_graphs = self.get_cleaned_data_for_step('settings')['respect_superset_graphs']
            tag_groups_tuples = self.get_tag_group_list(add_supersets,
                respect_superset_graphs)
            form.fields["tag_groups"].choices = tag_groups_tuples
            form.fields['tag_groups'].initial = [tg[0] for tg in tag_groups_tuples]
        return form

    def clear_cache(self):
        # Delete tag groups stored in session
        del self.request.session['available_tag_groups']
        self.request.modified = True

    def get_selected_tag_groups(self):
        tag_group_ids = self.get_cleaned_data_for_step('taggroups')['tag_groups']
        available_tag_groups = self.request.session.get('available_tag_groups')
        selected_tag_groups = {}
        for tid in tag_group_ids:
            selected_tag_groups[tid] = available_tag_groups[tid]
        return selected_tag_groups

    def done(self, form_list, **kwargs):
        """ Will add all missing links, stored in the tag groups field.
        """
        tag_groups = self.get_selected_tag_groups()
        unified_tag_groups = {}
        num_added_links = 0
        failed_links = {}
        for eg, group in tag_groups.items():
            for p, pdata in group['project_cgroots'].items():
                # Iterate missing links
                for ml in pdata['missing']:
                    try:
                        # Add missing link
                        wid = pdata['workspace']
                        oroot = ClassInstance.objects.get(pk=ml)
                        link_existing_classification(wid, self.request.user, p, oroot)
                        unified_tag_groups[eg] = group
                        num_added_links = num_added_links + 1
                    except Exception as e:
                        failed_links[ml] = e
        # Delete tag groups stored in session
        self.clear_cache()

        # Show final page
        return render_to_response('catmaid/classification/admin_done.html', {
            'tag_groups': unified_tag_groups,
            'num_added_links': num_added_links,
            'failed_links': failed_links,
        })

def classification_admin_view(request, *args, **kwargs):
   """ Wraps the class based ClassificationAdminWizard view in
   a function based view.
   """
   forms = [("settings", SettingsForm),
            ("taggroups", TagGroupSelectionForm),
            ("confirmation", ConfirmationForm)]
   view = ClassificationAdminWizard.as_view(forms)
   return view(request)
