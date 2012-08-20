import sys
import json
import re
import string

from string import upper
from collections import defaultdict
from pprint import pprint
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction, connection
from django.db.models import Count
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from vncbrowser.models import Project, Stack, Class, ClassInstance, \
    TreenodeClassInstance, ConnectorClassInstance, Relation, Treenode, \
    Connector, User, Textlabel, Location, TreenodeConnector, Double3D, \
    TextlabelLocation, Log, Message, ClassInstanceClassInstance
from vncbrowser.transaction import transaction_reportable_commit_on_success, RollbackAndReport

from catmaid.control.common import catmaid_can_edit_project, catmaid_login_optional, catmaid_login_required, my_render_to_response

from catmaid.control.common import insert_into_log, makeJSON_legacy_list, cursor_fetch_dictionary


@catmaid_login_optional
def projects(request, logged_in_user=None):
    # This is somewhat ridiculous - four queries where one could be
    # used in raw SQL.  The problem here is chiefly that
    # 'select_related' in Django doesn't work through
    # ManyToManyFields.  Development versions of Django have
    # introduced prefetch_related, but this isn't in the stable
    # version that I'm using.  (Another way around this would be to
    # query on ProjectStack, but the legacy CATMAID schema doesn't
    # include a single-column primary key for that table.)

    stacks = dict((x.id, x) for x in Stack.objects.all())

    # Create a dictionary that maps from projects to stacks:
    c = connection.cursor()  # @UndefinedVariable
    c.execute("SELECT project_id, stack_id FROM project_stack")
    project_to_stacks = defaultdict(list)
    for project_id, stack_id in c.fetchall():
        project_to_stacks[project_id].append(stacks[stack_id])

    # Find all the projects, and mark those that are editable from the
    # project_user table:
    if logged_in_user:
        projects = Project.objects.all()
        c.execute("SELECT project_id FROM project_user WHERE user_id = %s",
                  [logged_in_user.id])
        editable_projects = set(x[0] for x in c.fetchall())
    else:
        projects = Project.objects.filter(public=True)
        editable_projects = set([])

    # Find all the projects that are editable:
    catalogueable_projects = set(x.project.id for x in Class.objects.filter(class_name='driver_line').select_related('project'))

    # Create a dictionary with those results that we can output as JSON:
    result = {}
    for p in projects:
        if p.id not in project_to_stacks:
            continue
        stacks_dict = {}
        for s in project_to_stacks[p.id]:
            stacks_dict[s.id] = {
                'title': s.title,
                'comment': s.comment,
                'note': '',
                'action': 'javascript:openProjectStack(%d,%d)' % (p.id, s.id)}
        editable = p.id in editable_projects
        result[p.id] = {
            'title': p.title,
            'public_project': int(p.public),
            'editable': int(editable),
            'catalogue': int(p.id in catalogueable_projects),
            'note': '[ editable ]' if editable else '',
            'action': stacks_dict}
    return HttpResponse(json.dumps(result, sort_keys=True, indent=4), mimetype="text/json")


@catmaid_login_required
def labels_all(request, project_id=None, logged_in_user=None):
    qs = ClassInstance.objects.filter(
        class_column__class_name='label',
        project=project_id)
    return HttpResponse(json.dumps(list(x.name for x in qs)), mimetype="text/plain")


@catmaid_login_required
@transaction_reportable_commit_on_success
def labels_for_node(request, project_id=None, ntype=None, location_id=None, logged_in_user=None):
    if ntype == 'treenode':
        qs = TreenodeClassInstance.objects.filter(
            relation__relation_name='labeled_as',
            class_instance__class_column__class_name='label',
            treenode=location_id,
            project=project_id).select_related('class_instance')
    elif ntype == 'location' or ntype == 'connector':
        qs = ConnectorClassInstance.objects.filter(
            relation__relation_name='labeled_as',
            class_instance__class_column__class_name='label',
            connector=location_id,
            project=project_id).select_related('class_instance')
    else:
        raise Http404('Unknown node type: "%s"' % (ntype,))
    return HttpResponse(json.dumps(list(x.class_instance.name for x in qs)), mimetype="text/plain")


@catmaid_login_required
def labels_for_nodes(request, project_id=None, logged_in_user=None):
    nodes = [int(x, 10) for x in json.loads(request.POST['nods']).keys()]

    qs_treenodes = TreenodeClassInstance.objects.filter(
        relation__relation_name='labeled_as',
        class_instance__class_column__class_name='label',
        treenode__id__in=nodes,
        project=project_id).select_related('treenode', 'class_instance')

    qs_connectors = ConnectorClassInstance.objects.filter(
        relation__relation_name='labeled_as',
        class_instance__class_column__class_name='label',
        connector__id__in=nodes,
        project=project_id).select_related('connector', 'class_instance')

    result = defaultdict(list)

    for tci in qs_treenodes:
        result[tci.treenode.id].append(tci.class_instance.name)

    for cci in qs_connectors:
        result[cci.connector.id].append(cci.class_instance.name)

    return HttpResponse(json.dumps(result), mimetype="text/plain")


@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def label_update(request, project_id=None, location_id=None, ntype=None, logged_in_user=None):
    labeled_as_relation = Relation.objects.get(project=project_id, relation_name='labeled_as')
    p = get_object_or_404(Project, pk=project_id)
    if ntype == 'treenode':
        TreenodeClassInstance.objects.filter(
            treenode__id=location_id,
            relation=labeled_as_relation,
            class_instance__class_column__class_name='label').delete()
    elif ntype == 'connector' or ntype == 'location':
        ConnectorClassInstance.objects.filter(
            connector__id=location_id,
            relation=labeled_as_relation,
            class_instance__class_column__class_name='label').delete()
    else:
        raise Http404('Unknown node type: "%s"' % (ntype,))
    label_class = Class.objects.get(project=project_id, class_name='label')
    for tag_name in json.loads(request.POST['tags']):
        existing_tags = list(ClassInstance.objects.filter(
            project=p,
            name=tag_name,
            class_column=label_class))
        if len(existing_tags) < 1:
            tag = ClassInstance(
                project=p,
                name=tag_name,
                user=logged_in_user,
                class_column=label_class)
            tag.save()
        else:
            tag = existing_tags[0]
        if ntype == 'treenode':
            tci = TreenodeClassInstance(
                user=logged_in_user,
                project=p,
                relation=labeled_as_relation,
                treenode=Treenode(id=location_id),
                class_instance=tag)
        else:
            tci = ConnectorClassInstance(
                user=logged_in_user,
                project=p,
                relation=labeled_as_relation,
                connector=Connector(id=location_id),
                class_instance=tag)
        tci.save()
    return HttpResponse(json.dumps({'message': 'success'}), mimetype='text/json')


@catmaid_login_required
def user_list(request, logged_in_user=None):
    result = {}
    for u in User.objects.all().order_by('longname'):
        result[str(u.id)] = {
            "id": u.id,
            "name": u.name,
            "longname": u.longname}
    return HttpResponse(json.dumps(result), mimetype='text/json')


@catmaid_login_required
def root_for_skeleton(request, project_id=None, skeleton_id=None, logged_in_user=None):
    tn = Treenode.objects.get(
        project=project_id,
        parent__isnull=True,
        treenodeclassinstance__class_instance__id=skeleton_id)
    return HttpResponse(json.dumps({
                'root_id': tn.id,
                'x': tn.location.x,
                'y': tn.location.y,
                'z': tn.location.z}),
                        mimetype='text/json')




@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def update_textlabel(request, project_id=None, logged_in_user=None):
    params = {}
    parameter_names = ['tid', 'pid', 'x', 'y', 'z', 'text', 'type', 'r', 'g', 'b', 'a', 'fontname', 'fontstyle', 'fontsize', 'scaling']
    for p in parameter_names:
        params[p] = request.POST.get(p, None)
    # Rename params in to match model parameter names.
    params['font_name'] = params['fontname']
    params['font_style'] = params['fontstyle']
    params['font_size'] = params['fontsize']
    del params['fontname']
    del params['fontstyle']
    del params['fontsize']

    # Scaling is given 0 or 1 value by the caller, but our models use bool
    if params['scaling'] is not None:
        params['scaling'] = bool(int(params['scaling']))

    # Type must be either bubble or text.
    if params['type'] is not None:
        if (params['type'] != 'bubble'):
            params['type'] = 'text'

    response_on_error = ''
    try:
        response_on_error = 'Failed to find Textlabel with id %s.' % params['tid']
        label = Textlabel.objects.filter(id=params['tid'])[0]

        response_on_error = 'Failed to update Textlabel with id %s.' % params['tid']
        special_parameters = ['x', 'y', 'z', 'r', 'g', 'b', 'a', 'tid']
        # Set new values for label unless they haven't been specified or need
        # special handling.
        # for par in [p for p in parameter_names if p not in special_parameters]:
        for par in set(params.keys()).difference(special_parameters):
            if params[par] is not None:
                setattr(label, par, params[par])
        label.save()

        # If all parameters x, y and z have been specified, change the location
        if all([val is not None for val in [params[p] for p in ['x', 'y', 'z']]]):
            response_on_error = 'Failed to update the location of textlabel with id %s' % params['tid']
            TextlabelLocation.objects.filter(textlabel=params['tid']).update(
                    location=Double3D(float(params['x']), float(params['y']), float(params['z'])))

        return HttpResponse(' ')

    except RollbackAndReport:
        raise
    except Exception as e:
        if (response_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(response_on_error)


@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def delete_textlabel(request, project_id=None, logged_in_user=None):
    textlabel_id = request.POST.get('tid', None)

    if textlabel_id is None:
        raise RollbackAndReport('No treenode id provided.')

    response_on_error = ''
    try:
        response_on_error = 'Could not delete TextlabelLocations for treenode #%s' % textlabel_id
        TextlabelLocation.objects.filter(textlabel=textlabel_id).delete()
        response_on_error = 'Could not delete Textlabels for treenode #%s' % textlabel_id
        Textlabel.objects.filter(id=textlabel_id).delete()

    except RollbackAndReport:
        raise
    except Exception as e:
        if (response_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(response_on_error)

    return HttpResponse(json.dumps({'message': 'Success.'}))


@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def create_textlabel(request, project_id=None, logged_in_user=None):
    params = {}
    param_defaults = {
            'x': 0,
            'y': 0,
            'z': 0,
            'text': 'Edit this text...',
            'type': 'text',
            'r': 1,
            'g': 0.5,
            'b': 0,
            'a': 1,
            'font_name': False,
            'font_style': False,
            'font_size': False,
            'scaling': False}
    for p in param_defaults.keys():
        params[p] = request.POST.get(p, param_defaults[p])
    if (params['type'] != 'bubble'):
        params['type'] = 'text'

    new_label = Textlabel(
            text=params['text'],
            type=params['type'],
            scaling=params['scaling']
            )
    new_label.project_id = project_id
    if params['font_name']:
        new_label.font_name = params['font_name']
    if params['font_style']:
        new_label.font_style = params['font_style']
    if params['font_size']:
        new_label.font_size = params['font_size']
    new_label.save()

    TextlabelLocation(
            textlabel=new_label,
            location=Double3D(float(params['x']), float(params['y']), float(params['z']))).save()

    return HttpResponse(json.dumps({'tid': new_label.id}))


@catmaid_login_required
def most_recent_treenode(request, project_id=None, logged_in_user=None):
    skeleton_id = request.POST.get('skeleton_id', -1)
    treenode_id = request.POST.get('treenode_id', -1)

    try:
        tn = Treenode.objects\
        .filter(project=project_id,
                skeleton=skeleton_id,
                user=logged_in_user)\
        .extra(select={'most_recent': 'greatest(treenode.creation_time, treenode.edition_time)'})\
        .extra(order_by=['-most_recent'])[0]
    except IndexError:
        # TODO Not sure whether this is correct. This is the only place
        # where the treenode_id is used. Does it really have anything
        # to do with the query? The error message doesn't make much sense
        # either.
        return HttpResponse(json.dumps({'error': 'No skeleton and neuron found for treenode %s' % treenode_id}))

    return HttpResponse(json.dumps({
        'id': tn.id,
        'skeleton_id': tn.skeleton.id,
        'x': int(tn.location.x),
        'y': int(tn.location.y),
        'z': int(tn.location.z),
        # 'most_recent': str(tn.most_recent) + tn.most_recent.strftime('%z'),
        'most_recent': tn.most_recent.strftime('%Y-%m-%d %H:%M:%S.%f'),
        'type': 'treenode'
        }))


@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def delete_link(request, project_id=None, logged_in_user=None):
    connector_id = request.POST.get('connector_id', 0)
    treenode_id = request.POST.get('treenode_id', 0)

    links = TreenodeConnector.objects.filter(
            connector=connector_id,
            treenode=treenode_id)

    if (links.count() == 0):
        return HttpResponse(json.dumps({'error': 'Failed to delete connector #%s from geometry domain.' % connector_id}))

    links.delete()
    return HttpResponse(json.dumps({'result': 'Removed treenode to connector link'}))


@catmaid_login_required
@transaction.commit_on_success
def search(request, project_id=None, logged_in_user=None):
    def format_node_data(node):
        '''
        Formats node data for our json output.

        When we start using Django 1.4, we can use prefetch_related instead of using
        .values('treenode__xxx'), and will then be able to access a proper location
        object.
        '''
        location = Double3D.from_str(node['treenode__location'])
        return {
                'id': node['treenode'],
                'x': int(location.x),
                'y': int(location.y),
                'z': int(location.z),
                'skid': node['treenode__skeleton']}

    search_string = request.GET.get('substring', "")

    row_query = ClassInstance.objects.values('id', 'name', 'class_column__class_name').filter(
            name__icontains=search_string,
            project=project_id).order_by('class_column__class_name', 'name')
    rows = list(row_query)

    relation_map = get_relation_to_id_map(project_id)
    label_rows = {}
    for row in rows:
        # Change key-name of class_column__class_name for json output
        row['class_name'] = row.pop('class_column__class_name')
        # Prepare for retrieving nodes holding text labels
        if row['class_name'] == 'label':
            row['nodes'] = []
            label_rows[row['name']] = row

    node_query = TreenodeClassInstance.objects.filter(
            project=project_id,
            treenode__project=project_id,
            relation=relation_map['labeled_as'],
            class_instance__name__in=label_rows.keys())\
                    .order_by('-treenode__id')\
                    .values('treenode',
                            'treenode__location',
                            'treenode__skeleton',
                            'class_instance__name')
    # Insert nodes into their rows
    for node in node_query:
        row_with_node = label_rows[node['class_instance__name']]
        row_with_node['nodes'].append(format_node_data(node))

    # Delete the nodes property from rows with no nodes
    for row in rows:
        if 'nodes' in row and len(row['nodes']) == 0:
            del row['nodes']

    return HttpResponse(json.dumps(rows))


@catmaid_login_required
@transaction.commit_on_success
def list_logs(request, project_id=None, logged_in_user=None):
    user_id = int(request.POST.get('user_id', -1))  # We can see logs for different users
    display_start = int(request.POST.get('iDisplayStart', 0))
    display_length = int(request.POST.get('iDisplayLength', -1))
    if display_length < 0:
        display_length = 200  # Default number of result rows

    should_sort = request.POST.get('iSortCol_0', False)
    if should_sort:
        column_count = int(request.POST.get('iSortingCols', 0))
        sorting_directions = [request.POST.get('iSortDir_%d' % d) for d in range(column_count)]
        sorting_directions = map(lambda d: '-' if upper(d) == 'DESC' else '', sorting_directions)

        fields = ['user', 'operation_type', 'creation_time', 'x', 'y', 'z', 'freetext']
        sorting_index = [int(request.POST.get('iSortCol_%d' % d)) for d in range(column_count)]
        sorting_cols = map(lambda i: fields[i], sorting_index)

    log_query = Log.objects.filter(project=project_id)
    if user_id not in [-1, 0]:
        log_query = log_query.filter(user=user_id)
    log_query = log_query.extra(tables=['user'], where=['"log"."user_id" = "user"."id"'], select={
        'x': '("log"."location")."x"',
        'y': '("log"."location")."y"',
        'z': '("log"."location")."z"',
        'username': '"user"."name"',
        'timestamp': '''to_char("log"."creation_time", 'DD-MM-YYYY HH24:MI')'''
        })
    if should_sort:
        log_query = log_query.extra(order_by=[di + col for (di, col) in zip(sorting_directions, sorting_cols)])

    result = list(log_query[display_start:display_start + display_length])

    response = {'iTotalRecords': len(result), 'iTotalDisplayRecords': len(result), 'aaData': []}
    for log in result:
        response['aaData'] += [[
                log.username,
                log.operation_type,
                log.timestamp,
                log.x,
                log.y,
                log.z,
                log.freetext
                ]]

    return HttpResponse(json.dumps(response))






@catmaid_login_required
@transaction_reportable_commit_on_success
def tree_object_expand(request, project_id=None, logged_in_user=None):
    skeleton_id = request.POST.get('skeleton_id', None)
    if skeleton_id is None:
        raise RollbackAndReport('A skeleton id has not been provided!')
    else:
        skeleton_id = int(skeleton_id)

    relation_map = get_relation_to_id_map(project_id)

    # Treenode is element_of class_instance (skeleton), which is model_of (neuron)
    # which is part_of class_instance (?), recursively, until reaching class_instance
    # ('root').

    response_on_error = ''
    try:
        # 1. Retrieve neuron id of the skeleton
        response_on_error = 'Cannot find neuron for the skeleton with id: %s' % skeleton_id
        neuron_id = ClassInstanceClassInstance.objects.filter(
                project=project_id,
                relation=relation_map['model_of'],
                class_instance_a=skeleton_id)[0].class_instance_b_id

        path = [skeleton_id, neuron_id]

        while True:
            # 2. Retrieve all the nodes of which the neuron is a part of.
            response_on_error = 'Cannot find parent instance for instance with id: %s' % path[-1]
            parent = ClassInstanceClassInstance.objects.filter(
                    project=project_id,
                    class_instance_a=path[-1],
                    relation=relation_map['part_of']).values(
                            'class_instance_b',
                            'class_instance_b__class_column__class_name')[0]
            path.append(parent['class_instance_b'])
            if 'root' == parent['class_instance_b__class_column__class_name']:
                break

        path.reverse()
        return HttpResponse(json.dumps(path))

    except RollbackAndReport:
        raise
    except Exception as e:
        if (response_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(response_on_error)


@catmaid_login_required
def stats(request, project_id=None, logged_in_user=None):
    qs = Treenode.objects.filter(project=project_id)
    qs = qs.values('user__name').annotate(count=Count('user__name'))
    result = {'users': [],
              'values': []}
    for d in qs:
        result['values'].append(d['count'])
        user_name = '%s (%d)' % (d['user__name'], d['count'])
        result['users'].append(user_name)
    return HttpResponse(json.dumps(result), mimetype='text/json')


@catmaid_login_required
def stats_summary(request, project_id=None, logged_in_user=None):
    result = {
        'proj_users': User.objects.filter(project=project_id).count(),
        'proj_treenodes': Treenode.objects.filter(project=project_id).count(),
        'proj_textlabels': Textlabel.objects.filter(project=project_id).count()}
    for key, class_name in [('proj_neurons', 'neuron'),
                            ('proj_synapses', 'synapse'),
                            ('proj_skeletons', 'skeleton'),
                            ('proj_presyn', 'presynaptic terminal'),
                            ('proj_postsyn', 'postsynaptic terminal'),
                            ('proj_tags', 'label')]:
        result[key] = ClassInstance.objects.filter(
            project=project_id,
            class_column__class_name=class_name).count()
    return HttpResponse(json.dumps(result), mimetype='text/json')


@catmaid_login_required
@transaction_reportable_commit_on_success
def skeleton_ancestry(request, project_id=None, logged_in_user=None):
    # All of the values() things in this function can be replaced by
    # prefetch_related when we upgrade to Django 1.4 or above
    skeleton_id = request.POST.get('skeleton_id', None)
    if skeleton_id is None:
        raise RollbackAndReport('A skeleton id has not been provided!')

    relation_map = get_relation_to_id_map(project_id)
    for rel in ['model_of', 'part_of']:
        if rel not in relation_map:
            raise RollbackAndReport(' => "Failed to find the required relation %s' % rel)

    response_on_error = ''
    try:
        response_on_error = 'The search query failed.'
        neuron_rows = ClassInstanceClassInstance.objects.filter(
                class_instance_a=skeleton_id,
                relation=relation_map['model_of']).values(
                        'class_instance_b',
                        'class_instance_b__name')
        neuron_count = neuron_rows.count()
        if neuron_count == 0:
            raise RollbackAndReport('No neuron was found that the skeleton %s models' % skeleton_id)
        elif neuron_count > 1:
            raise RollbackAndReport('More than one neuron was found that the skeleton %s models' % skeleton_id)

        parent_neuron = neuron_rows[0]
        ancestry = []
        ancestry.append({
            'name': parent_neuron['class_instance_b__name'],
            'id': parent_neuron['class_instance_b'],
            'class': 'neuron'})

        # Doing this query in a loop is horrible, but it should be very rare
        # for the hierarchy to be more than 4 deep or so.  (This is a classic
        # problem of not being able to do recursive joins in pure SQL.) Just
        # in case a cyclic hierarchy has somehow been introduced, limit the
        # number of parents that may be found to 10.
        current_ci = parent_neuron['class_instance_b']
        for i in range(10):
            response_on_error = 'Could not retrieve parent of class instance %s' % current_ci
            parents = ClassInstanceClassInstance.objects.filter(
                    class_instance_a=current_ci,
                    relation=relation_map['part_of']).values(
                            'class_instance_b__name',
                            'class_instance_b',
                            'class_instance_b__class_column__class_name')
            parent_count = parents.count()
            if parent_count == 0:
                break  # We've reached the top of the hierarchy.
            elif parent_count > 1:
                raise RollbackAndReport('More than one class_instance was found that the class_instance %s is part_of.' % current_ci)
            else:
                parent = parents[0]
                ancestry.append({
                    'name': parent['class_instance_b__name'],
                    'id': parent['class_instance_b'],
                    'class': parent['class_instance_b__class_column__class_name']
                    })
                current_ci = parent['class_instance_b']

        return HttpResponse(json.dumps(ancestry))

    except RollbackAndReport:
        raise
    except Exception as e:
        if (response_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(response_on_error)


def get_relation_to_id_map(project_id):
    result = {}
    for r in Relation.objects.filter(project=project_id):
        result[r.relation_name] = r.id
    return result


def get_class_to_id_map(project_id):
    result = {}
    for r in Class.objects.filter(project=project_id):
        result[r.class_name] = r.id
    return result


@catmaid_can_edit_project
@transaction_reportable_commit_on_success
def node_update(request, project_id=None, logged_in_user=None):
    nodes = {}
    for key, value in request.POST.items():
        parsed_key = re.search('^(?P<property>[a-zA-Z_]+)(?P<node_index>[0-9]+)$', key)
        if not parsed_key:
            continue
        node_index = parsed_key.group('node_index')
        node_property = parsed_key.group('property')
        if node_index not in nodes:
            nodes[node_index] = {}
        nodes[node_index][node_property] = value

    required_properties = ['node_id', 'x', 'y', 'z', 'type']
    for node_index, node in nodes.items():
        for req_prop in required_properties:
            if req_prop not in node:
                raise RollbackAndReport('Missing key: %s in index %s' % (req_prop, node_index))

        try:
            if node['type'] == 'treenode':
                Treenode.objects.filter(id=node['node_id']).update(
                        user=logged_in_user,
                        location=Double3D(float(node['x']), float(node['y']), float(node['z'])))
            elif node['type'] == 'connector':
                Location.objects.filter(id=node['node_id']).update(
                        user=logged_in_user,
                        location=Double3D(float(node['x']), float(node['y']), float(node['z'])))
            else:
                raise RollbackAndReport('Unknown node type: %s' % node['type'])
        except:
            raise RollbackAndReport('Failed to update treenode: %s' % node['node_id'])

    return HttpResponse(json.dumps({'updated': len(nodes)}))


@catmaid_login_required
@transaction_reportable_commit_on_success
def node_nearest(request, project_id=None, logged_in_user=None):
    params = {}
    param_defaults = {
            'x': 0,
            'y': 0,
            'z': 0,
            'skeleton_id': -1,
            'neuron_id': -1}
    for p in param_defaults.keys():
        params[p] = request.POST.get(p, param_defaults[p])
    relation_map = get_relation_to_id_map(project_id)

    if params['skeleton_id'] < 0 and params['neuron_id'] < 0:
        raise RollbackAndReport('You must specify either a skeleton or a neuron')

    for rel in ['part_of', 'model_of']:
        if rel not in relation_map:
            raise RollbackAndReport('Could not find required relation %s for project %s.' % (rel, project_id))

    skeletons = []
    if params['skeleton_id'] > 0:
        skeletons.append(params['skeleton_id'])

    message_on_error = ''
    try:
        if params['neuron_id'] > 0:  # Add skeletons related to specified neuron
            # Assumes that a cici 'model_of' relationship always involves a
            # skeleton as ci_a and a neuron as ci_b.
            message_on_error = 'Finding the skeletons failed.'
            neuron_skeletons = ClassInstanceClassInstance.objects.filter(
                class_instance_b=params['neuron_id'],
                relation=relation_map['model_of'])
            for neur_skel_relation in neuron_skeletons:
                skeletons.append(neur_skel_relation.class_instance_a_id)

        # Get all treenodes connected to skeletons
        message_on_error = 'Finding the treenodes failed.'
        treenodes = Treenode.objects.filter(project=project_id, skeleton__in=skeletons)

        def getNearestTreenode(x, y, z, treenodes):
            minDistance = -1
            nearestTreenode = None
            for tn in treenodes:
                xdiff = x - tn.location.x
                ydiff = y - tn.location.y
                zdiff = z - tn.location.z
                distanceSquared = xdiff ** 2 + ydiff ** 2 + zdiff ** 2
                if distanceSquared < minDistance or minDistance < 0:
                    nearestTreenode = tn
                    minDistance = distanceSquared
            return nearestTreenode

        nearestTreenode = getNearestTreenode(
                int(params['x']),
                int(params['y']),
                int(params['z']),
                treenodes)
        if nearestTreenode is None:
            raise RollbackAndReport('No treenodes were found.')

        # TODO Check if callers really need string data.
        # Return string data to emulate behavior of pg_fetch_assoc.
        return HttpResponse(json.dumps({
            'treenode_id': str(nearestTreenode.id),
            'x': str(int(nearestTreenode.location.x)),
            'y': str(int(nearestTreenode.location.y)),
            'z': str(int(nearestTreenode.location.z)),
            'skeleton_id': str(nearestTreenode.skeleton_id)}))

    except RollbackAndReport:
        raise
    except Exception as e:
        if (message_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(message_on_error)


@catmaid_login_required
@transaction_reportable_commit_on_success
def node_list(request, project_id=None, logged_in_user=None):
    # TODO This function is used very often and Catmaid would benefit from its
    # optimization. The following things should have big effects, ordered by
    # expected efficiencty gain VS effort to implement.

    # - Stop using cursor_fetch_dictionary, dictionary creation requires an
    # entire iteration over each query result list. Instead access each
    # selected column directly.

    # - Do not use the two separate dictionaries treenodes_by_id and
    # connectors_by_id. Instead, just append newly queries data onto a result
    # list. This will require manually keeping track of connectors and their
    # places in the list when collecting their relation properties.

    # - Remove a connector's top level relation properties when it is first
    # encountered (when implementing the optimization above), instead of doing
    # so in a separate iteration over the result list/dictionary.

    params = {}
    for p in ('z', 'top', 'left', 'width', 'height', 'zres', 'as'):
        params[p] = int(request.GET.get(p, 0))
    params['limit'] = 2000  # Limit the number of retrieved treenodes.
    params['zbound'] = 1.0  # The scale factor to volume bound the query in z-direction based on the z-resolution.
    params['project_id'] = project_id

    relation_map = get_relation_to_id_map(project_id)
    class_map = get_class_to_id_map(project_id)

    for class_name in ['skeleton']:
        if class_name not in class_map:
            raise RollbackAndReport('Can not find "%s" class for this project' % class_name)

    for relation in ['presynaptic_to', 'postsynaptic_to', 'model_of', 'element_of']:
        if relation not in relation_map:
            raise RollbackAndReport('Can not find "%s" relation for this project' % relation)

    response_on_error = ''
    try:
        c = connection.cursor()
        # Fetch treenodes which are in the bounding box:
        response_on_error = 'Failed to query treenodes.'
        c.execute('''
        SELECT treenode.id AS id,
            treenode.parent_id AS parentid,
            (treenode.location).x AS x,
            (treenode.location).y AS y,
            (treenode.location).z AS z,
            treenode.confidence AS confidence,
            treenode.user_id AS user_id,
            treenode.radius AS radius,
            ((treenode.location).z - %(z)s) AS z_diff,
            skeleton_id,
            'treenode' AS type
        FROM treenode
        WHERE
            treenode.project_id = %(project_id)s
            AND (treenode.location).x >= %(left)s
            AND (treenode.location).x <= (%(left)s + %(width)s)
            AND (treenode.location).y >= %(top)s
            AND (treenode.location).y <= (%(top)s + %(height)s)
            AND (treenode.location).z >= (%(z)s - %(zbound)s * %(zres)s)
            AND (treenode.location).z <= (%(z)s + %(zbound)s * %(zres)s)
        LIMIT %(limit)s
        ''', params)

        treenodes_by_id = {}
        treenodes_q = cursor_fetch_dictionary(c)
        for tn in treenodes_q:
            treenodes_by_id[tn['id']] = tn

        # Now, if an ID for the active skeleton was supplied, make sure
        # that all treenodes for that skeleton are added:
        if (params['as'] != 0):
            response_on_error = "Failed to query active skeleton's (id %s) treenodes" % params['as']
            c.execute('''
            SELECT treenode.id AS id,
                treenode.parent_id AS parentid,
                (treenode.location).x AS x,
                (treenode.location).y AS y,
                (treenode.location).z AS z,
                treenode.confidence AS confidence,
                treenode.user_id AS user_id,
                treenode.radius AS radius,
                ((treenode.location).z - %(z)s) AS z_diff,
                skeleton_id,
                'treenode' AS type
            FROM treenode
            WHERE
                skeleton_id = %(as)s
            ''', params)
            active_skeleton_treenodes = cursor_fetch_dictionary(c)

            for tn in active_skeleton_treenodes:
                treenodes_by_id[tn['id']] = tn

        params['zbound'] = 4.1
        # Retrieve connectors that are synapses - do a LEFT OUTER JOIN with
        # the treenode_connector table, so that we get entries even if the
        # connector is not connected to any treenodes
        response_on_error = 'Failed to query connector locations.'
        c.execute('''
        SELECT connector.id AS id,
            (connector.location).x AS x,
            (connector.location).y AS y,
            (connector.location).z AS z,
            connector.confidence AS confidence,
            connector.user_id AS user_id,
            ((connector.location).z - %(z)s) AS z_diff,
            treenode_connector.relation_id AS treenode_relation_id,
            treenode_connector.treenode_id AS tnid,
            treenode_connector.confidence AS tc_confidence,
            'connector' AS type
        FROM connector LEFT OUTER JOIN treenode_connector
            ON treenode_connector.connector_id = connector.id
        WHERE connector.project_id = %(project_id)s AND
            (connector.location).x >= %(left)s AND
            (connector.location).x <= (%(left)s + %(width)s) AND
            (connector.location).y >= %(top)s AND
            (connector.location).y <= (%(top)s + %(height)s) AND
            (connector.location).z >= (%(z)s - %(zbound)s * %(zres)s) AND
            (connector.location).z <= (%(z)s + %(zbound)s * %(zres)s)
        ORDER BY id, z_diff LIMIT %(limit)s
        ''', params)
        connector_relations = cursor_fetch_dictionary(c)

        # Check for any treenodes that those connectors are linked to that
        # weren't either in the active skeleton or in the bounding box.
        # This is so that we can draw arrows from any displayed connector
        # to all of its connected treenodes, even if one is several slices
        # below.

        missing_treenode_ids = []
        for cn in connector_relations:
            if cn['tnid'] is not None and cn['tnid'] not in treenodes_by_id:
                missing_treenode_ids.append(cn['tnid'])
        params['missing_treenode_ids'] = ', '.join(map(str, missing_treenode_ids))

        if len(missing_treenode_ids) > 0:
            response_on_error = 'Failed to query treenodes from connectors.'
            c.execute('''
            SELECT treenode.id AS id,
                treenode.parent_id AS parentid,
                (treenode.location).x AS x,
                (treenode.location).y AS y,
                (treenode.location).z AS z,
                treenode.confidence AS confidence,
                treenode.user_id AS user_id,
                treenode.radius AS radius,
                ((treenode.location).z - %(z)s) AS z_diff,
                skeleton_id,
                'treenode' AS type
            FROM treenode
            WHERE id IN (%(missing_treenode_ids)s)
            ''', params)
            connector_treenodes = cursor_fetch_dictionary(c)

            for tn in connector_treenodes:
                treenodes_by_id[tn['id']] = tn

        # For each connector, collect its relation properties.
        connectors_by_id = {}
        for cn in connector_relations:
            cn_id = 'con_%s' % cn['id']

            # Enter a connector only once, and update the existing entry when
            # we encounter new connector entries with the same id (but other
            # relations) so that all relations are collected in one entry.
            if cn_id not in connectors_by_id:
                connectors_by_id[cn_id] = cn

            cn_entry = connectors_by_id[cn_id]

            if cn['tnid'] is not None:  # We have a relationship to add to the connector.
                tnid = cn['tnid']
                if cn['treenode_relation_id'] == relation_map['presynaptic_to']:
                    relationship = 'pre'
                else:
                    relationship = 'post'
                tc_confidence = cn['tc_confidence']

                # Ensure there is an array to hold relationships of this type.
                if relationship not in cn_entry:
                    cn_entry[relationship] = []

                cn_entry[relationship].append({
                    'tnid': tnid,
                    'confidence': tc_confidence})

        # Clean out connector relation info from the top level of each
        # connector, as it has been added to relationship arrays at this point.
        for cn in connectors_by_id.values():
            for key in ['tnid', 'treenode_relation_id', 'tc_confidence']:
                if key in cn:
                    del(cn[key])

        return HttpResponse(json.dumps(treenodes_by_id.values() + connectors_by_id.values()))

    except RollbackAndReport:
        raise
    except Exception as e:
        if (response_on_error == ''):
            raise RollbackAndReport(str(e))
        else:
            raise RollbackAndReport(response_on_error)
