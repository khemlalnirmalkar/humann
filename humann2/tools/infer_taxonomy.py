#!/usr/bin/env python

from __future__ import print_function # Python 2.7+ required
import sys
import csv
import argparse
import util

description = """
HUMAnN2 utility for inferring "unclassified" taxonomy
=====================================================
Based on the lowest common ancestor (LCA) annotation
of each UniRef50/90 cluster, infer approximate taxonomy 
for unclassified features at a target level of resolution 
(default=Family). Will modify features of known genus/species 
to match target level.

Requires the following supplmental data files:

uniref50-tol-lca.dat.gz (for UniRef50)
uniref90-tol-lca.dat.gz (for UniRef90)

Which can be downloaded from:
https://bitbucket.org/biobakery/humann2/src/tip/humann2/data/misc/
"""

# ---------------------------------------------------------------
# constants
# ---------------------------------------------------------------

c_root = "Root"
c_levels = [
    c_root,
    "Kingdom",
    "Phylum",
    "Class",
    "Order",
    "Family",
    "Genus",
]
c_tmode = "totals"
c_umode = "unclassified"
c_smode = "stratified"
c_tol_header = "# TOL"
c_lca_header = "# LCA"
c_unclassified = "unclassified"

# ---------------------------------------------------------------
# helper objects
# ---------------------------------------------------------------

class Taxon:
    def __init__( self, name, rank, parent_name ):
        self.name = name
        self.rank = rank
        self.parent_name = parent_name

class TreeOfLife:
    def __init__( self ):
        self.nodes = {}
        self.root = Taxon( c_root, c_root, None )
        self.nodes[self.root.name] = self.root
    def attach( self, node ):
        if node.name not in self.nodes:
            self.nodes[node.name] = node
        else:
            print( "Taxon <{}> already defined".format( node.name ), file=sys.stderr )
    def get_lineage( self, name ):
        lineage = []
        while name in self.nodes and name != self.root.name:
            node = self.nodes[name]
            lineage.append( [node.rank, node.name] )
            name = node.parent_name
        lineage.append( [self.root.rank, self.root.name] )
        return lineage

# ---------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------

def get_args ():
    """ Get args from Argparse """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawTextHelpFormatter
        )
    parser.add_argument( "-i", "--input", 
                         required=True,
                         help="HUMAnN2 output table" )
    parser.add_argument( "-o", "--output", 
                         default=None,
                         help="Destination for modified table; default=STDOUT" )
    parser.add_argument( "-l", "--level",
                         choices=c_levels,
                         default="Family",
                         help="Desired level for taxonomic estimation/summation; default=Family" )
    parser.add_argument( "-d", "--datafile", 
                         default="uniref50-tol-lca.dat.gz", 
                         help="Location of the uniref(50/90)-tol-lca data file; default=<HERE>" )
    parser.add_argument( "-m", "--mode", 
                         choices=[c_tmode, c_umode, c_smode],
                         default=c_tmode,
                         help="Which rows to include in the estimation/summation; default=TOTALS" )
    parser.add_argument( "-t", "--threshold", 
                         type=float, 
                         default=1e-3, 
                         help="Minimum frequency for a new taxon to be included; default=1e-3" )
    args = parser.parse_args()
    return args

# ---------------------------------------------------------------
# utilities
# ---------------------------------------------------------------

def build_taxmap( features, target_rank, p_datafile ):
    unirefs = {k.split( util.c_strat_delim )[0] for k in features}
    unirefs = {k.split( util.c_name_delim )[0] for k in unirefs}
    unirefs = {k for k in unirefs if "UniRef" in k}
    # load tree of life, subset uniref lca annotation and add to taxmap
    tol = TreeOfLife()
    taxmap = {}
    tol_mode = False
    lca_mode = False
    with util.try_zip_open( p_datafile ) as fh:
        print( "Loading taxonomic data from: "+p_datafile, file=sys.stderr )
        for row in csv.reader( fh, csv.excel_tab ):
            if row[0] == c_tol_header:
                print( "  Loading TOL data", file=sys.stderr )
                tol_mode = True
                continue
            if row[0] == c_lca_header:
                print( "  Loading LCA data", file=sys.stderr )
                tol_mode = False
                lca_mode = True
                continue
            if tol_mode:
                name, rank, parent_name = row
                tol.attach( Taxon( name, rank, parent_name ) )
            elif lca_mode:
                uni, lca = row
                if uni in unirefs:
                    for rank, name in tol.get_lineage( lca ):
                        if rank == target_rank:
                            taxmap[uni] = rank.lower()[0] + "__" + name
                            break
    # augment taxmap with genus-level lineage information for stratified features
    for feature in features:
        feature, name, stratum = util.fsplit( feature )
        if stratum is not None and "g__" in stratum:
            genus = stratum.split( util.c_taxon_delim )[0]
            if target_rank == "Genus":
                taxmap[stratum] = genus
            else:
                genus = genus.replace( "g__", "" )
                for rank, name in tol.get_lineage( genus ):
                    if rank == target_rank:
                        taxmap[stratum] = rank.lower()[0] + "__" + name
                        break
    return taxmap

def tax_connect( feature, taxmap ):
    old = feature
    feature, name, stratum = util.fsplit( feature )
    if stratum is None or stratum == c_unclassified:
        stratum2 = taxmap.get( feature, c_unclassified )
    else:
        stratum2 = taxmap.get( stratum, c_unclassified )
    return util.fjoin( feature, name, stratum2 )

# ---------------------------------------------------------------
# main
# ---------------------------------------------------------------

def main( ):
    args = get_args( )
    tbl = util.Table( args.input )
    # build the taxmap
    print( "Building taxonomic map for input table", file=sys.stderr )
    taxmap = build_taxmap( tbl.rowheads, args.level, args.datafile )
    # refine the taxmap
    counts = {}
    for old, new in taxmap.items():
        counts[new] = counts.get( new, 0 ) + 1
    total = float( sum( counts.values( ) ) )
    count = {k:v/total for k, v in counts.items()}
    taxmap = {old:new for old, new in taxmap.items() if count[new] >= args.threshold}
    # reindex the table
    print( "Reindexing the input table", file=sys.stderr )
    ticker = util.Ticker( tbl.rowheads )
    index = {}
    for i, rowhead in enumerate( tbl.rowheads ):
        ticker.tick()
        feature, name, stratum = util.fsplit( rowhead )
        new_rowhead = tax_connect( rowhead, taxmap )
        # unmapped is never stratified
        if feature == util.c_unmapped:
            index.setdefault( rowhead, [] ).append( i )
        # outside of unclassfied mode, keep totals
        if stratum is None and args.mode != c_umode:
            index.setdefault( rowhead, [] ).append( i )
            # in totals mode, guess at taxnomy from uniref name
            if args.mode == c_tmode:
                index.setdefault( new_rowhead, [] ).append( i )
        elif stratum == c_unclassified and args.mode == c_umode:
            # in unclassified mode, make a new row for the total...
            index.setdefault( util.fjoin( feature, name, None ), [] ).append( i )
            # ...then replace "unclassified" with inferred taxonomy
            index.setdefault( new_rowhead, [] ).append( i )
        elif stratum is not None and args.mode == c_smode:
            index.setdefault( new_rowhead, [] ).append( i )
    # rebuild the table
    print( "Rebuilding the input table", file=sys.stderr )
    rowheads2, data2 = [], []
    ticker = util.Ticker( index )
    for rowhead in util.fsort( index ):
        ticker.tick()
        rowheads2.append( rowhead )
        newrow = [0 for k in tbl.colheads]
        for i in index[rowhead]:
            oldrow = map( float, tbl.data[i] )
            newrow = [a + b for a, b in zip( newrow, oldrow )]
        data2.append( newrow )
    tbl.rowheads = rowheads2
    tbl.data = data2
    # output
    print( "Writing new table", file=sys.stderr )
    tbl.write( args.output, unfloat=True )
    # report on performance
    success, total = 0, 0
    for rowhead in tbl.rowheads:
        feature, name, stratum = util.fsplit( rowhead )
        if stratum is not None:
            total += 1
            if stratum != c_unclassified:
                success += 1
    print( "Summary: Of {TOTAL} stratifications, {SUCCESS} mapped at {TARGET} level ({PERCENT}%)".format( 
            TOTAL=total, 
            SUCCESS=success, 
            TARGET=args.level, 
            PERCENT=round( 100 * success / float( total ), 1 ),
            ), file=sys.stderr,
           )

if __name__ == "__main__":
    main( )
