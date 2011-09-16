/* -*- mode: espresso; espresso-indent-level: 2; indent-tabs-mode: nil -*- */
/* vim: set softtabstop=2 shiftwidth=2 tabstop=2 expandtab: */

// TODO connectors are screwed up when zooming in/out
// TODO check all other TODOS

/** Namespace where Node instances are created and edited. */
var SkeletonElements = new function()
{
  var active_skeleton_color = "rgb(255,255,0)";
  var inactive_skeleton_color = "rgb(255,0,255)";
  var inactive_skeleton_color_above = "rgb(0,0,255)";
  var inactive_skeleton_color_below = "rgb(255,0,0)";
  var root_node_color = "rgb(255, 0, 0)";

  var TYPE_NODE = "treenode";
  var TYPE_CONNECTORNODE = "connector";

  var CATCH_RADIUS = 8;

  var DISABLED = -1; // ID of the disabled nodes

  // Two arrays containing all created Node and ConnectorNode, for their reuse.
  var nodePool = [];
  var connectorPool = [];
  // The two corresponding indices in the pool for the next available instance for reuse
  var nextNodeIndex = 0;
  var nextConnectorIndex = 0;
  var firstDisabledNodeIndex = -1;

  this.resetCache = function() {
    nextNodeIndex = 0;
    nextConnectorIndex = 0;
  };

  this.clearCache = function() {
    nodePool = [];
    connectorPool = [];
    nextNodeIndex = 0;
    nextConnectorIndex = 0;
    firstDisabledNodeIndex = -1;
  }

  /** Disable all cached Node instances at or beyond the cutoff index. */
  this.disableBeyond = function(nodeCuttoff, connectorCuttoff) {
    var i;
    for (i = nodeCuttoff; i < nodePool.length; ++i) {
      disableNode(nodePool[i]);
    }
    for (i = connectorCuttoff; i < connectorPool.length; ++i) {
      disableConnectorNode(connectorPool[i]);
    }

    //console.log(nodePool.length, nextNodeIndex, nodeCuttoff);
  };

  /** Surrogate constructor that may reuse an existing, cached Node instance currently not in use.
   * Appends any newly created instances to the pool. */
  this.newNode = function(
    id, // unique id for the node from the database
    paper, // the raphael paper this node is drawn to
    parent, // the parent node, if present within the subset of nodes retrieved for display; otherwise null.
    r, // the radius
    x, // the x coordinate in pixel coordinates
    y, // y coordinates
    z, // z coordinates
    zdiff, // the different from the current slices
    skeleton_id,
    is_root_node) // the id of the skeleton this node is an element of
  {
    var node;
    if (nextNodeIndex < nodePool.length) {
      node = nodePool[nextNodeIndex];
      reuseNode(node, id, r, x, y, z, zdiff, skeleton_id, is_root_node);
      if (is_root_node && node.line) node.line.hide();
    } else {
      node = new this.Node(id, paper, parent, r, x, y, z, zdiff, skeleton_id, is_root_node);
      nodePool.push(node);
      if (node.line) node.line.toBack();
    }
    nextNodeIndex += 1;
    return node;
  };

  /** Constructor for Node instances. */
  this.Node = function(
    id, // unique id for the node from the database
    paper, // the raphael paper this node is drawn to
    parent, // the parent node
    r, // the radius
    x, // the x coordinate in pixel coordinates
    y, // y coordinates
    z, // z coordinates
    zdiff, // the different from the current slices
    skeleton_id,
    is_root_node) // the id of the skeleton this node is an element of
  {
    this.id = id;
    this.type = TYPE_NODE;
    this.paper = paper;
    this.parent = parent;
    this.children = {};
    this.connectors = {};
    this.r = r < 0 ? 3 : r;
    this.x = x;
    this.y = y;
    this.z = z;
    this.zdiff = zdiff;
    this.skeleton_id = skeleton_id;
    this.isroot = is_root_node;
    this.fillcolor = inactive_skeleton_color;
    this.c = null; // The Raphael circle for drawing
    this.mc = null; // The Raphael circle for mouse actions (it's a bit larger)
    this.line = paper.path(); // The Raphael line element that represents an edge between nodes

    // The member functions:
    this.setXY = setXY;
    this.drawEdges = nodeDrawEdges;
    this.draw = draw;
    this.deleteall = nodeDeleteAll;
    this.deletenode = nodeDelete;
    this.setColor = setColor;
    this.colorFromZDiff = nodeColorFromZDiff;
    this.createCircle = createCircle;

    // Init block
    // 1. Add this node to the parent's children if it exists
    if (parent) parent.children[id] = this;
  };

  /** Before reusing a node, clear all the member variables that
   * are relevant to the skeleton structure.
   * All numeric variables will be overwritten,
   * and the c, mc and line will be reused. */
  var disableNode = function(node)
  {
    node.id = DISABLED;
    node.parent = null;
    node.children = {};
    node.connectors = {};
    if (node.c) {
      node.c.hide();
      node.mc.hide();
    }
    if (node.line) {
      node.line.hide();
    }
  };

  /** Takes an existing Node and sets all the proper members as given, and resets the children and connectors. */
  var reuseNode = function(node, id, r, x, y, z, zdiff, skeleton_id, isroot)
  {
    node.id = id;
    node.parent = null;
    node.children = {};
    node.connectors = {};
    node.r = r < 0 ? 3 : r;
    node.x = x;
    node.y = y;
    node.z = z;
    node.zdiff = zdiff;
    node.skeleton_id = skeleton_id;
    node.isroot = isroot;

    if (node.c) {
      if (0 !== zdiff) {
        node.c.hide();
        node.mc.hide();
      } else {
        var newCoords = {cx: x, cy: y};
        node.c.attr(newCoords);
        node.mc.attr(newCoords);
      }
    }
  };

  /** Trigger the redrawing of the lines with parent, children and connectors.
   * Here, 'this' is the node, given that it is called in the context of the node only.
   */
  var nodeDrawEdges = function(toChildren) {
    var ID,
        children = this.children,
        connectors = this.connectors;
    
    if (toChildren) {
      for (ID in children) {
        if (children.hasOwnProperty(ID)) {
          drawLineToParent(children[ID]);
        }
      }
    }

    for (ID in connectors) {
      if (connectors.hasOwnProperty(ID)) {
        connectors[ID].drawEdges();
      }
    }
    if (this.parent !== null) {
      drawLineToParent(this);
    }
  };

  /** Update the local x,y coordinates of the node
   * Update them for the raphael objects as well.
   * Does NOT redraw the edges.
   * Here 'this' refers to the node.
   */
  var setXY = function(xnew, ynew)
  {
    this.x = xnew;
    this.y = ynew;
    if (this.c) {
      this.c.attr({
        cx: xnew,
        cy: ynew
      });
      this.mc.attr({
        cx: xnew,
        cy: ynew
      });
    }
  };

  /** Updates the coordinates of the raphael path
   * that represents the line from the node to the parent.
   */
  var drawLineToParent = function (node) {
    var parent = node.parent;
    if (parent && node.line) {
      node.line.attr({
        path: [
          ["M", node.x, node.y],
          ["L", parent.x, parent.y]
        ],
        stroke: node.colorFromZDiff(parent.zdiff, parent.skeleton_id),
        "stroke-width": 2
      });
      // May be hidden if the node was reused
      if ("none" === node.line.node.style.display) { node.line.show(); }
    }
  };

  /** Recreate the GUI components, namely the circle and edges.
   * Here 'this' refers to the node.
   *  This is called only when creating a single node
   */
  var draw = function() {
    this.drawEdges();
    this.createCircle();
  };

  /** Delete all objects relevant to the node
  * such as raphael DOM elements and node references
  * javascript's garbage collection should do the rest.
   * Here 'this' refers to the node.
   * TODO this function is never used? */
  var nodeDeleteAll = function()
  {
    // Test if there is any child of type ConnectorNode
    // If so, it is not allowed to remove the treenode
    var i,
        children = this.children,
        parent = this.parent;
    // Remove the parent of all the children
    for (i in children) {
      if (children.hasOwnProperty(i)) {
        children[i].line.remove();
        children[i].parent = null;
      }
    }
    // Remove the raphael svg elements from the DOM
    if (this.c) {
      this.c.remove();
      this.mc.remove();
    }
    if (parent !== null) {
      this.line.remove();
      var pc = parent.children;
      // remove this node from parent's children list
      for (i in pc) {
        if (pc.hasOwnProperty(i)) {
          if (pc[i].id === id) {
            // FIXME: use splice(1,1) instead
            delete pc[i];
          }
        }
      }
    }
  };

  /** Delete the node from the database and removes it from
   * the current view and local objects.
   * Here 'this' refers to the node.
   */
  var nodeDelete = function () {
    var node = this;
    requestQueue.register("model/treenode.delete.php", "POST", {
      pid: project.id,
      tnid: node.id
    }, function (status, text) {
      if (status !== 200) {
        alert("The server returned an unexpected status (" + status + ") " + "with error message:\n" + text);
      } else {
        // activate parent node when deleted
        if (node.parent) {
          node.paper.catmaidSVGOverlay.selectNode(node.parent.id);
        } else {
          // TODO fetch parent id from the database and activate it
          node.paper.catmaidSVGOverlay.activateNode(null);
        }
        // Redraw everything for now
        node.paper.catmaidSVGOverlay.updateNodes();


        // TODO something is wrong, in that upon deleting a node updateNodes() is called like 10 times in a row.
        // TODO   but cannot reproduce it always.

      }
      return true;
    });
  };

  /** Set the node fill color depending on its distance from the
  * current slice, whether it's the active node, the root node, or in
  * an active skeleton.
   * Here 'this' refers to the node. */
  var setColor = function ()
  {
    if (this.id === SkeletonAnnotations.getActiveNodeId()) {
      // The active node is always in green:
      this.fillcolor = SkeletonAnnotations.getActiveNodeColor();
    } else if (this.isroot) {
      // The root node should be colored red unless it's active:
      this.fillcolor = root_node_color;
    } else {
      // If none of the above applies, just colour according to the z difference.
      this.fillcolor = this.colorFromZDiff(this.zdiff, this.skeleton_id);
    }

    if (this.c) {
      this.c.attr({
        fill: this.fillcolor
      });
    }
  };

  /** Return a color depending upon some conditions,
   * such as whether the zdiff with the current section is positive, negative, or zero,
   * and whether the node belongs to the active skeleton.
   */
  var nodeColorFromZDiff = function(zdiff, skeleton_id)
  {
    if (zdiff > 0) {
      return inactive_skeleton_color_above;
    } else if (zdiff < 0) {
      return inactive_skeleton_color_below;
    } else if (skeleton_id === SkeletonAnnotations.getActiveSkeletonId() ) {
      return active_skeleton_color;
    }
    return inactive_skeleton_color
  };

  /** Create the Raphael circle elements if and only if the zdiff is zero, that is, if the node lays on the current section.
   * Here 'this' refers to the node.
   * */
  var createCircle = function()
  {
    // TODO this could improve. For example the objects given as arguments could be reused forever, given that raphael merely reads them
    // TODO    and that javascript is single-threaded (at least when it comes to creating nodes in overlay.js).
    if (0 === this.zdiff) {
      var paper = this.paper;
      // c and mc may already exist if the node is being reused
      if (this.c && this.mc) {
      } else {
        // create a raphael circle object
        this.c = paper.circle(this.x, this.y, this.r);
        // a raphael circle oversized for the mouse logic
        this.mc = paper.circle(this.x, this.y, CATCH_RADIUS);
      }

      this.c.attr({
        fill: this.fillcolor,
        stroke: "none",
        opacity: 1.0
      });

      this.mc.attr({
        fill: "rgb(0, 1, 0)",
        stroke: "none",
        opacity: 0
      });

      if ("none" === this.c.node.style.display) {
        this.c.show();
        this.mc.show();
      }

      this.mc.catmaidNode = this; // for event handlers

      assignEventHandlers(this.mc, this.type);
    }
  };


  /** Event handling functions for 'mc'
  * Realize that:
  *    mc.prev === c
  * and that, on constructing the mc, we declared:
  *    mc.catmaidNode = this;  // 'this' is the node
   *
   * Below, the function() is but a namespace that returns the actual nodeAssignEventHandlers function,
   * which assigns the event handlers to the mc given to it as argument.
  */
  var assignEventHandlers = function ()
  {
    /** Variables used for mouse events, which involve a single node at a time.
     * These are set at mc_start and then used at mc_move. */
    var ox, oy;

    /** Here 'this' is mc. */
    var mc_dblclick = function(e) {
      // TODO these sliders don't exist anymore
      if (e.altKey) {
        // zoom in
        slider_trace_s.move(-1);
      }
      else {
        // zoom out
        slider_trace_s.move(1);
      }
      this.paper.catmaidSVGOverlay.tracingCommand('goactive');
    };

    /**  Log information in the status bar when clicked on the node
     *
     * Here 'this' is mc, and treenode is the Node instance
     */
    var mc_click = function(e) {
      var node = this.catmaidNode,
          paper = this.paper;
      if (e.shiftKey) {
        var atnID = SkeletonAnnotations.getActiveNodeId();
        if ((e.ctrlKey || e.metaKey) && e.shiftKey) {
          // if it is active node, set active node to null
          if (node.id === atnID) {
            paper.catmaidSVGOverlay.activateNode(null);
          }
          statusBar.replaceLast("Deleted node #" + node.id);
          node.deletenode();
          e.stopPropagation();
          return true;
        }
        if (atnID) {
          var atnType = SkeletonAnnotations.getActiveNodeType();
          // connected activated treenode or connectornode
          // to existing treenode or connectornode
          console.log("from source #" + atnID + " to target #" + node.id);
          if (atnType === TYPE_CONNECTORNODE) {
            paper.catmaidSVGOverlay.createLink(atnID, node.id, "postsynaptic_to", "synapse", "postsynaptic terminal", "connector", "treenode");
            // TODO check for error
            statusBar.replaceLast("Joined node #" + atnID + " to connector #" + node.id);
          } else if (atnType === TYPE_NODE) {
            paper.catmaidSVGOverlay.createTreenodeLink(atnID, node.id);
            // TODO check for error
            statusBar.replaceLast("Joined node #" + atnID + " to node #" + node.id);
          }

        } else {
          alert("Nothing to join without an active node!");
        }
        e.stopPropagation();

      } else {
        // activate this node
        paper.catmaidSVGOverlay.activateNode(node);
        // stop propagation of the event
        e.stopPropagation();
      }
    };

    /** Here 'this' is mc, and treenode is the Node instance. */
    var mc_move = function(dx, dy) {
      var node = this.catmaidNode,
        mc = this,
        c = this.prev;

      node.x = ox + dx;
      node.y = oy + dy;
      c.attr({
        cx: node.x,
        cy: node.y
      });
      mc.attr({
        cx: node.x,
        cy: node.y
      });
      node.drawEdges(true); // TODO for connector this is overkill
      statusBar.replaceLast("Moving node #" + node.id);

      node.needsync = true;
    };

    /** Here 'this' is mc. */
    var mc_up = function() {
      var c = this.prev;
      c.attr({
        opacity: 1
      });
    };

    /** Here 'this' is mc, and treenode is the Node instance. */
    var mc_start = function() {
      var node = this.catmaidNode,
        c = this.prev;
      ox = node.x;
      oy = node.y;
      c.attr({
        opacity: 0.7
      });
      this.paper.catmaidSVGOverlay.activateNode(node);
    };

    var mc_mousedown = function(e) {
      e.stopPropagation();
    };

    var connector_mc_click = function(e) {
      var atnID = SkeletonAnnotations.getActiveNodeId(),
          connectornode = this.catmaidNode,
          paper = this.paper;
      // return some log information when clicked on the node
      // this usually refers here to the mc object
      if (e.shiftKey) {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey) {
          if (connectornode.id === atnID) {
            paper.catmaidSVGOverlay.activateNode(null);
          }
          statusBar.replaceLast("Deleted connector #" + connectornode.id);
          connectornode.deletenode();
          e.stopPropagation();
          return true;
        }
        if (atnID) {
          // connected activated treenode or connectornode
          // to existing treenode or connectornode
          console.log("from source #", atnID, "to connector #", connectornode.id);
          paper.catmaidSVGOverlay.createLink(atnID, connectornode.id, "presynaptic_to", "presynaptic terminal", "synapse", "treenode", "connector");
          statusBar.replaceLast("Joined node #" + atnID + " with connector #" + connectornode.id);
        } else {
          var g = $('body').append('<div id="growl-alert" class="growl-message"></div>').find('#growl-alert');
          g.growlAlert({
            autoShow: true,
            content: 'You need to activate a node before joining it to a connector node!',
            title: 'BEWARE',
            position: 'top-right',
            delayTime: 2500,
            onComplete: function() { g.remove(); }
          });
        }
        e.stopPropagation();
      } else {
        //console.log("Try to activate node");
        // activate this node
        paper.catmaidSVGOverlay.activateNode(connectornode);
        // stop propagation of the event
        e.stopPropagation();
      }
    };

    // The actual assignEventHandlers function
    // BEWARE that 'this' cannot be used to refer to the node within this function
    return function(mc, type) {
      mc.drag(mc_move, mc_start, mc_up);
      mc.mousedown(mc_mousedown);
      mc.dblclick(mc_dblclick);

      if (TYPE_NODE === type) {
        mc.click(mc_click);
      } else {
        // TYPE_CONNECTORNODE
        mc.click(connector_mc_click);
      }
    }
  }();


  // TODO must reuse nodes instead of creating them new, to avoid DOM insertions.
  // -- well, it can: just leave as members of each the functions that are really different.

  // Identical functions: setXY, setColor, createCircle, deleteAll, deletenode (but for the php URL), some of the sub-functions of createEventHandlers

  // Also, there shouldn't be a "needsync" flag. Instead, push the node to an array named "needSyncWithDB". Will avoid looping.

  // Regarding the nodes map: it should be an array of keys over objects stored in a a cache of nodes that are already inserted into the DOM
  // and that can be reused.
  // Regarding children and connectors: any reason not to make them plain arrays? Given that they are always small,
  // using a filter to find a node with a specific id would be enough.

  // WARNING deleteall is never used!


  /** Surrogate cosntructor for ConnectorNode.
   * See "newNode" for explanations. */
  this.newConnectorNode = function(
    id, // unique id for the node from the database
    paper, // the raphael paper this node is drawn to
    r, // radius
    x, // the x coordinate in pixel coordinates
    y, // y coordinates
    z, // z coordinates
    zdiff) // the different from the current slices
  {
    var connector;
    if (nextConnectorIndex < connectorPool.length) {
      connector = connectorPool[nextConnectorIndex];
      reuseConnectorNode(connector, id, r, x, y, z, zdiff);
    } else {
      connector = new this.ConnectorNode(id, paper, r, x, y, z, zdiff);
      connectorPool.push(connector);
    }
    nextConnectorIndex += 1;
    return connector;
  };

  /**
   * Constructor for ConnectorNode.
   */
  this.ConnectorNode = function (
    id, // unique id for the node from the database
    paper, // the raphael paper this node is drawn to
    r, // radius
    x, // the x coordinate in pixel coordinates
    y, // y coordinates
    z, // z coordinates
    zdiff) // the different from the current slices
  {
    this.id = id;
    this.type = TYPE_CONNECTORNODE; // TODO update this name!
    this.needsync = false; // state variable; whether this node is already synchronized with the database
    this.x = x; // local screen coordinates relative to the div, in pixel coordinates
    this.y = y;
    this.z = z;
    this.zdiff = zdiff;
    this.paper = paper;
    this.pregroup = {}; // set of presynaptic treenodes
    this.postgroup = {}; // set of postsynaptic treenodes
    this.r = r; // prefixed radius for now
    this.c = null; // The Raphael circle for drawing
    this.mc = null; // The Raphael circle for mouse actions (it's a bit larger)
    this.preLines = {}; // The Raphael edges to the presynaptic nodes
    this.postLines = {}; // The Raphael edges to the postsynaptic nodes
    this.fillcolor = null;

    // Member functions
    this.setXY = setXY;
    this.setColor = setColor;
    this.colorFromZDiff = connectorColorFromZDiff;
    this.createCircle = createCircle;
    this.deletenode = connectorDelete;
    this.draw = draw;
    this.drawEdges = connectorDrawEdges;
  };


  /**
   * @param c The Node to reuse
   * @param id
   * @param r
   * @param x
   * @param y
   * @param z
   * @param zdiff
   */
  var reuseConnectorNode = function(c, id, r, x, y, z, zdiff)
  {
    c.id = id;
    c.r = r;
    c.x = x;
    c.y = y;
    c.z = z;
    c.zdiff = zdiff;
    c.pregroup = {};
    c.postgroup = {};

    if (c.c) {
      var newCoords = {cx: x, cy: y};
      c.c.attr(newCoords);
      c.mc.attr(newCoords);
    }

    // preLines and postLines are always removed and then recreated when calling drawEdges
  }

  /**
   *
   * @param c The ConnectorNode instance to disable
   */
  var disableConnectorNode = function(c) {
    if (c.c) {
      c.c.hide();
      c.mc.hide();
    }
    removeConnectorEdges(c.preLines, c.postLines);
  }

  /** Here 'this' is the connector node. */
  var connectorColorFromZDiff =  function(zdiff)
  {
    if (zdiff > 0) {
      return "rgb(0, 0, 255)";
    } else if (zdiff < 0) {
      return "rgb(255, 0, 0)";
    } else {
      return "rgb(235, 117, 0)";
    }
  };

  /** Delete the connector from the database and removes it from
   * the current view and local objects.
   * Here 'this' is the connector node.
   */
  var connectorDelete = function ()
  {
    var connectornode = this;
    requestQueue.register("model/connector.delete.php", "POST", {
      pid: project.id,
      cid: connectornode.id,
      class_instance_type: 'synapse'
    }, function (status, text, xml) {
      if (status !== 200) {
        alert("The server returned an unexpected status (" + status + ") " + "with error message:\n" + text);
      } else {
          if (text && text !== " ") {
            var e = $.parseJSON(text);
            if (e.error) {
              alert(e.error);
            } else {
              // Refresh all nodes in any case, to reflect the new state of the database
              connectornode.paper.catmaidSVGOverlay.updateNodes();
              return true;
            }
          }
      }
    });
  };

  var removeConnectorEdges = function(preLines, postLines) {
    var i;
    for (i in preLines) {
      if (preLines.hasOwnProperty(i)) {
        if (preLines[i].remove)
          preLines[i].remove();
        else console.log(i, preLines[i]);
      }
    }

    for (i in postLines) {
      if (postLines.hasOwnProperty(i)) {
        if (postLines[i].remove)
          postLines[i].remove();
        else console.log(i, postLines[i]);
      }
    }
  };

  /**
   * Here 'this' is the connector node.
   */
  var connectorDrawEdges = function()
  {
    var i,
        preLines = this.preLines,
        postLines = this.postLines,
        pregroup = this.pregroup,
        postgroup = this.postgroup;

    removeConnectorEdges(preLines, postLines);

    // re-create
    for (i in pregroup) {
      if (pregroup.hasOwnProperty(i)) {
        preLines[pregroup[i].id] = connectorCreateLine(this, pregroup[i].id, true);
      }
    }

    for (i in postgroup) {
      if (postgroup.hasOwnProperty(i)) {
        postLines[postgroup[i].id] = connectorCreateLine(this, postgroup[i].id, false);
      }
    }
  };

  /** Below, a function that acts as a namespace and assigns to connectorCreateLine the proper function.
   * (Notice how it is executed at the end of its declaration. */
  var connectorCreateLine = function()
  {
    /** Constructor method for ArrowLine. */
    var ArrowLine = function (paper, x1, y1, x2, y2, size, strowi, strocol) {
      // Compute position for arrowhead pointer
      var rloc = 9;
      var xdiff = (x2 - x1);
      var ydiff = (y2 - y1);
      var le = Math.sqrt(xdiff * xdiff + ydiff * ydiff);
      var x1new = (x1 - x2) * (1 - rloc / le) + x2;
      var y1new = (y1 - y2) * (1 - rloc / le) + y2;
      var x2new = (x2 - x1) * (1 - rloc / le) + x1;
      var y2new = (y2 - y1) * (1 - rloc / le) + y1;

      var angle = Math.atan2(x1 - x2, y2 - y1);
      angle = (angle / (2 * Math.PI)) * 360;
      var linePath = paper.path("M" + x1new + " " + y1new + " L" + x2new + " " + y2new);
      var arrowPath = paper.path("M" + x2new + " " + y2new + " L" + (x2new - size) + " " + (y2new - size) + " L" + (x2new - size) + " " + (y2new + size) + " L" + x2new + " " + y2new).attr("fill", "black").rotate((90 + angle), x2new, y2new);
      linePath.attr({
        "stroke-width": strowi,
        "stroke": strocol
      });
      arrowPath.attr({
        "fill": strocol,
        "stroke": strocol
      });
      // The 'this' refers to the new ArrowLine
      this.remove = function () {
        arrowPath.remove();
        linePath.remove();
      };
    };

    // Return the actual connectorCreateLine function
    return function(self, to_id, pre) {
      if (pre) {
        return new ArrowLine(self.paper, self.pregroup[to_id].x, self.pregroup[to_id].y, self.x, self.y, 5, 2, "rgb(126, 57, 112)");
      } else {
        return new ArrowLine(self.paper, self.x, self.y, self.postgroup[to_id].x, self.postgroup[to_id].y, 5, 2, "rgb(67, 67, 128)");
      };
    }
  }();


};
