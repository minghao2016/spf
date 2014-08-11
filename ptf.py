# PTF inference
import argparse
import sys
import scipy
from scipy.misc import factorial
from scipy.special import digamma, gamma, gammaln
import numpy as np
import random #TODO: just use one source for random, not both np and this
from collections import defaultdict
import time
import ptfstore
from time import clock


def save_state(dire, iteration, model, params):
    #print "Saving state!"
    fname = "%s-iter%d.out" % (dire + '/model', iteration)
    ptfstore.dump(fname, model, params)

def log_state(f, iteration, params, likelihood):
    f.write("%d\t%e\t%e\t%e\t%e\t%e\t%e\n" % (iteration, likelihood, \
        params.theta.mean(), params.beta.mean(), params.tau.mean(),
        params.eta, params.inter.mean()))

def get_predictions(model, params, data, test_set):
    preds = np.zeros(len(test_set.users))

    if model.intercept:
        preds += params.inter[test_set.items]
    elif model.trust:
        preds = np.ones(len(test_set.users))*1e-10


    if model.iat or model.MF:
        M = (params.theta[test_set.users] * params.beta[test_set.items]).sum(axis=1)
    
    if model.MF:
        preds += M
    
    if model.trust or model.iat:
        T = np.zeros(len(test_set.users))
        for i in xrange(len(test_set.users)):
            user = test_set.users[i]
            item = test_set.items[i]
            for vser in data.friends[user]:
                if model.binary:
                    if item in data.user_data[vser]:
                        T[i] += params.tau.get(user, vser)
                else:
                    rating_v = data.sparse_ratings.get(item, vser) 
                    if rating_v != 0:
                        T[i] += params.tau.get(user, vser) * rating_v
            
            if data.friend_counts[user][item] != 0 and not model.nofdiv:
                T[i] /= data.friend_counts[user][item] #NOFDIV
        #for i in range(10):
        #    print "<%.3f, %.3f, %.3f, %.3f>   %.3f" % (M[i], T[i], params.eta * M[i] * T[i], params.inter[test_set.items][i], M[i]+ T[i]+ params.eta * M[i] * T[i] + params.inter[test_set.items][i])

    if model.trust:
        preds += T

    if model.iat:
        if model.trust:
            preds += params.eta * M * T
        else:
            preds += M * T
    
    return preds

def get_log_likelihoods(model, params, data, test_set):
    predictions = get_predictions(model, params, data, test_set)
    likelihoods = np.log(predictions) * test_set.ratings - \
        np.log(factorial(test_set.ratings)) - predictions
    #print "LIKELIHOODS"
    #print predictions[:10]
    #print test_set.ratings[:10]
    #print "LIKIHOODS END"
    return likelihoods


def get_elbo(model, priors, params, data, logf):
    #print "getting log likelihoods for ratings"
    rating_likelihoods = get_log_likelihoods(model, params, data, data.validation)
    
    elbo = rating_likelihoods.sum()

    # theta
    elbo_theta = (-1*priors['b_theta'] + params.b_theta) * np.array(params.theta)
    elbo_theta += (priors['a_theta'] - params.a_theta) * np.array(np.log(params.theta)) 
    elbo_theta += priors['a_theta']*np.log(priors['b_theta']) - \
        params.a_theta * np.log(params.b_theta)
    elbo_theta += gammaln(params.a_theta) - gammaln(priors['a_theta']) 

    # beta
    elbo_beta = (-1*priors['b_beta'] + params.b_beta) * np.array(params.beta)
    elbo_beta += (priors['a_beta'] - params.a_beta) * np.array(np.log(params.beta)) 
    elbo_beta += priors['a_beta']*np.log(priors['b_beta']) - \
        params.a_beta * np.log(params.b_beta)
    elbo_beta += gammaln(params.a_beta) - gammaln(priors['a_beta']) 

    # tau
    if model.trust:
        print "elbo_tau progression..."
        elbo_tau = (params.tau.multiply((priors['b_tau'].const_multiply(-1) + params.b_tau))).sum()
        print "   ",elbo_tau
        elbo_tau += (((priors['a_tau'] - params.a_tau)).multiply(params.tau.log())).sum() 
        print "   ",elbo_tau
        elbo_tau += (priors['a_tau'].multiply(priors['b_tau'].log())).sum() 
        print "   ",elbo_tau
        elbo_tau -= (params.a_tau.multiply(params.b_tau.log())).sum()
        print "   ",elbo_tau
        elbo_tau += params.a_tau.log_gamma_sum()
        print "   ",elbo_tau
        elbo_tau -= priors['a_tau'].log_gamma_sum()
        print "   ",elbo_tau
    else:
        elbo_tau = 0
    
    # eta
    if model.iat:
        print "**********"
        print params.a_eta, params.b_eta, params.eta
        elbo_eta = (-1*priors['b_eta'] + params.b_eta) * params.eta
        elbo_eta += (priors['a_eta'] - params.a_eta) * np.log(params.eta)
        elbo_eta += priors['a_eta']*np.log(priors['b_eta']) - params.a_eta * np.log(params.b_eta)
        elbo_eta += gammaln(params.a_eta) - gammaln(priors['a_eta']) 
    else:
        elbo_eta = 0.0
   
    # intercept; b never varies from prior
    if model.intercept:
        elbo_inter = (priors['a_inter'] - params.a_inter) * np.log(params.inter)
        elbo_inter += priors['a_inter']*np.log(params.b_inter) - params.a_inter * np.log(params.b_inter)
        elbo_inter += gammaln(params.a_inter) - gammaln(priors['a_inter']) 
    else:
        elbo_inter = np.zeros(3)
    
    
    elbo_ratings = elbo
    elbo += elbo_theta.sum()
    elbo += elbo_beta.sum()
    elbo += elbo_tau
    elbo += elbo_eta
    elbo += elbo_inter.sum()
    #print "ELBO: ratings: %f\ttheta: %f\tbeta: %f\ttau: %f\teta: %f\tinter: %f\t=> total: %f" % \
    #    (elbo_ratings, elbo_theta.sum(), elbo_beta.sum(), elbo_tau, elbo_eta, elbo_inter.sum(), elbo)
    logf.write("%f\t%f\t%f\t%f\t%f\t%f\t%f\n" % \
        (elbo_ratings, elbo_theta.sum(), elbo_beta.sum(), elbo_tau, elbo_eta, elbo_inter.sum(), elbo))
    return elbo


def get_ave_likelihood(model, params, data, test_set):
    return get_log_likelihoods(model, params, data, test_set).sum() \
        / test_set.size

class model_settings:
    def __init__(self, K, MF, trust, iat, intercept, users, items, undirected, binary, sorec=False):
        self.K = K
        self.MF = MF
        self.trust = trust
        self.iat = iat
        self.intercept = intercept
        self.users = users # hashes for users and items
        self.items = items
        self.undirected = undirected
        self.binary = binary
        self.sorec = sorec

    #@classmethod
    #def new(self, user_count, item_count, K, MF, trust, iat, intercept, users, items, undirected):
    #    return model_settings(user_count, item_count, args.K, MF, \
    #        trust, iat, intercept, users, items, undirected)
    
    @classmethod
    def fromargs(self, args):
        MF = False if args.model == "trust" or args.model == "IATonly" \
            else True
        trust = False if args.model == 'PF' or args.model == "IATonly" \
            or args.model == "IAT+MF" else True
        iat = True if args.model == 'PTFv2' or args.model == "IATonly" \
            or args.model == "IAT+MF" else False # interaction term
        #sorec = True if args.sorec else False

        return model_settings(args.K, MF, \
            trust, iat, args.intercept, {}, {}, args.undirected, args.binary)

class parameters:
    def __init__(self, model, readonly, priors=False, data=False):
        self.readonly = readonly
        print "  in parameters init (readonly=%s)" % str(readonly)    

        print "   initializing model parameters"
        self.tau = dict_matrix(float, model.user_count, model.user_count)
        self.eta = 0
        self.inter = np.zeros(model.item_count)
        self.theta = np.zeros((model.user_count, model.K))
        self.beta = np.zeros((model.item_count, model.K))
        
        if not readonly:
            print "    initializing intermediate variables"
            self.a_theta = np.ones((model.user_count, model.K))
            self.a_beta = np.ones((model.item_count, model.K))
            self.tau = np.zeros(3) #TODO: fix; this is heer for the logfile
            if model.trust or model.iat:
                self.b_theta = np.ones((model.user_count, model.K))
                self.b_beta = np.ones((model.item_count, model.K))
                #self.tau = data.friend_matrix.copy(mult=10.0)
                self.tau = data.friend_matrix.copy()#mult=0.1)
            else:
                self.b_theta = np.ones((model.user_count, model.K)) #TODO: do this outside if
                self.b_beta = np.ones((model.item_count, model.K))
           
            # per item intercepts
            self.a_inter = np.ones(model.item_count)
            self.b_inter = np.ones(model.item_count)*model.user_count# * 1e-9
            print "model.user_count", model.user_count
            if model.intercept:
                self.inter = np.ones(model.item_count) * 0.1
            else:
                self.inter = np.zeros(model.item_count)
            self.eta = 0.1 #TODO: init from args

    def set_to_priors(self, priors):
        self.a_theta.fill(priors['a_theta'])
        self.b_theta.fill(priors['b_theta'])
        self.a_beta.fill(priors['a_beta'])
        self.b_beta.fill(priors['b_beta'])
        self.a_tau = priors['a_tau'].copy()
        self.b_tau = priors['b_tau'].copy()
        self.a_inter.fill(priors['a_inter']) 
        self.a_eta = priors['a_eta']
        self.b_eta = priors['b_eta']

   
    def update_shape(self, user, item, rating, model, data, user_scale=1.0):
        #print "      updating shape for user %d and item %d" % (user,item)
        phi_M = self.theta[user] * self.beta[item]
        start = clock()
        if model.trust or model.iat:
            if not model.binary:
                phi_T = self.tau.multiply_row(user, \
                    data.sparse_ratings, item, \
                    1.0 if model.nofdiv else max(1, data.friend_counts[user][item]))
                    #NOFDIV
            else:
                phi_T = self.tau.multiply_row_select(user, \
                    data.user_data, item, \
                    1 if model.nofdiv else max(1, data.friend_counts[user][item]))
                    #NOFDIV
        else:
            phi_T = dict_row(float, 1)
        
        # this section is the problematic one
        phi_sum = self.inter[item]
        if model.MF:
            phi_sum += phi_M.sum()
        if model.trust:
            phi_sum += phi_T.sum()
        if model.iat:
            #phi_MT_sum, phi_MT1, phi_MT0 = phi_T.get_sums(phi_M  * self.eta)

            phi_MT = np.mat(phi_M  * self.eta).T * phi_T.densedense()
            phi_sum += phi_MT.sum()
            #phi_sum += phi_MT_sum
        mult = rating / phi_sum
    
        if model.intercept:
            self.a_inter[item] += self.inter[item] * mult * user_scale # for binary data, this fixes intercept max at 1
        
        #if abs(self.beta[0,0] - self.beta[1,0]) < 1e-4 and item == 3729:
        #    print self.a_beta[item]
        #    print self.a_beta[item+1]
        #    print '--'
        if model.MF: 
            #self.a_theta[user] += phi_M * mult * user_scale
            self.a_theta[user] += phi_M * mult 
            self.a_beta[item] += phi_M * mult * user_scale
        #if abs(self.beta[0,0] - self.beta[1,0]) < 1e-4 and item == 3729:
        #    print self.a_beta[item]
        #    print self.a_beta[item+1]
        #    print '--'
        if model.trust:
            if phi_T.sum() != 0:
                self.a_tau.row_add(user, phi_T, mult=mult)
                #if user == 0:# and item > 1780:
                #    print item, phi_M.sum(), phi_T.sum(), self.inter[item], mult
        
        #print user_scale, mult 

        if model.iat:
            M_update = phi_MT.sum(axis=1) * mult
            T_update = phi_MT.sum(axis=0) * mult
            #M_update = phi_MT1 * mult
            #T_update = phi_MT0 * mult

            #self.a_theta[user] += M_update.flat * user_scale
            self.a_theta[user] += M_update.flat
            #self.a_theta[user] += M_update
            self.a_beta[item] += (M_update * user_scale).flat
            #self.a_beta[item] += M_update * user_scale
            if T_update.sum() != 0:
                self.a_tau.row_add(user, T_update.flat, phi_T.cols.keys())
                #self.a_tau.row_add(user, T_update, phi_T.cols.keys())
            if model.trust:
                self.a_eta += float(phi_MT.sum() * mult) * user_scale
                #self.a_eta += phi_MT_sum * mult * user_scale
  

    def update_MF(self, model, data, user_scale=1.0, \
                users_updated=False, \
                items_updated=False, items_seen_counts=False, tau0=1, kappa=1):
        if users_updated == False:
            users_updated = set(model.users.values())
            items_updated = set(model.items.values())
        #print "user_scale: ", user_scale

        usrs = list(users_updated)
        if model.MF:
            self.b_theta[usrs] += self.beta.sum(axis=0)
            #self.b_theta[usrs] += self.beta[itms].sum(axis=0)
        if model.iat:
            for user in users_updated:
                user_row = dict_row(float, model.item_count)
                for vser in data.friends[user]:
                    trust = self.tau.get(user, vser)
                    if model.binary:
                        for item in data.user_data[vser]:
                            user_row.item_add(item, trust)
                    else:
                        for item, rating in data.user_data[vser]:
                            user_row.item_add(item, trust * rating)
                
                for item in user_row.cols.keys():
                    if data.friend_counts[user][item] == 0:
                        continue
                    user_row.cols[item] *= self.eta / \
                        (1.0 if model.nofdiv else \
                        data.friend_counts[user][item]) #NOFDIV
                    self.b_theta[user] += user_row.cols[item] * self.beta[item]
                
                self.theta[user] = self.a_theta[user] / self.b_theta[user]
                for item in user_row.cols.keys():
                    self.b_beta[item] += user_row.cols[item] * self.theta[user] * user_scale
        else:
            for user in users_updated:
                self.theta[user] = self.a_theta[user] / self.b_theta[user]

        if model.MF:
            self.b_beta += self.theta.sum(axis=0)
            #self.b_beta[list(items_updated)] += self.theta.sum(axis=0)
            #self.b_beta[itms] += user_scale * self.theta[usrs].sum(axis=0)
        
        
        for item in items_updated:
            #if item == 1:
            #    print "pre-update beta: ", self.beta[item]
            if items_seen_counts:
                rho = (items_seen_counts[item] + tau0) ** (-kappa)
            else:
                rho = 1
            self.beta[item] = \
                self.beta[item] * (1 - rho) + \
                (self.a_beta[item] / self.b_beta[item]) * rho
            #if item == 1:
            #    print "post-update beta: ", self.beta[item], " (rho: ", rho, " & new: ", (self.a_beta[item] / self.b_beta[item]), ")"

    def update_TF(self, model, data, user_scale=1.0, \
                users_updated=False, iteration=1, tau0=1, kappa=1):
        if iteration == 1 or users_updated == False:
            rho = 1
        else:
            rho = (iteration + tau0) ** (-kappa)
        #rho = (iteration + tau0) ** (-kappa)
        if users_updated == False:
            users_updated = set(model.users.values())
        #itms = list(items_updated)
        #usrs = list(users_updated)

        # these only need to be updated if there's an interation term "iat"
        if not model.iat:
            if model.trust:
                self.tau = self.a_tau / self.b_tau

                #user = 0
                #vser = data.friends[user][0]
                #print "***************"
                #user = 30
                #print "user 30 has %d friends" % len(data.friends[user])
                #for vser in data.friends[user]:
                #    print vser, self.a_tau.get(user,vser),  self.b_tau.get(user,vser), \
                #        self.a_tau.get(user,vser) / self.b_tau.get(user,vser), self.tau.rows[user][vser]
            return
        
        for user in users_updated:
            MF = {}
            for vser in data.friends[user]:
                uv = 0
                for dat in data.user_data[vser]:
                    if model.binary:
                        item = dat
                    else:
                        item, rating = dat
                    if item not in MF:
                        MF[item] = sum(self.theta[user] * self.beta[item])
                    if model.binary:
                        update = MF[item] \
                            / (1 if model.nofdiv else data.friend_counts[user][item])
                            #\ NOFDIV
                    else:
                        update = rating * MF[item] \
                            / (1 if model.nofdiv else data.friend_counts[user][item])
                            #/ data.friend_counts[user][item] NOFDIV
                    self.b_tau.item_add(user, vser, \
                        self.eta * update)
                    uv += update
                self.tau.set(user, vser, self.a_tau.get(user,vser) / \
                    self.b_tau.get(user,vser))

                if model.trust:
                    self.b_eta += uv * self.tau.get(user,vser) * user_scale
        
        if model.trust:
            self.eta = (1-rho) * self.eta + rho * float(self.a_eta / self.b_eta)
        

class triplets:
    def __init__(self):
        self.users = []
        self.items = []
        self.ratings = []
        self.friend_counts = []

class dict_row():
    def __init__(self, val_type, ncols=False):
        self.ncols = ncols

        self.val_type = val_type

        self.cols = defaultdict(val_type)

    def set(self, col, val):
        if val == 0 and col in self.cols:
            del self.cols[col]
        elif val != 0:
            self.cols[col] = val

    def item_add(self, col, val):
        if val != 0: #not (type(val) == int or type(val) == float) or val != 0:
            self.cols[col] += val
  
    def sum(self):
        rv = 0
        for col in self.cols:
            rv += self.cols[col]
        return rv 

    def densedense(self):
        return self.cols.values()

    def pre_multiply(self, other):
        #TODO: check for shape/size; this presumes both are rows
        #TODO: scalar multiplication
        # make a matrix
        rv = dict_matrix(self.val_type, other.shape[0])
        for col in self.cols:
            for row in xrange(other.shape[0]):
                #print (row, col, self.cols[col], other[row], self.cols[col] * other[row])
                rv.set(row, col, self.cols[col] * other[row])
        return rv

    def const_multiply(self, const):
        rv = dict_row(self.val_type, self.ncols)
        for col in self.cols:
            rv.set(col, self.cols[col] * const)
        return rv

    def todense(self):
        rv = np.zeros(self.ncols)
        for col in self.cols:
            rv[col] = self.cols[col]
        return rv
    
    def get_sums(self, mult):
        phi_MT_sum = 0.0
        phi_MT1 = np.zeros(len(mult))
        phi_MT0 = np.zeros(len(self.cols))

        i = 0
        for v in self.cols.values():
            c = mult * v
            cs = sum(c)
            phi_MT += cs
            phi_MT0[i] += cs
            phi_MT1 += c
            i += 1

        return phi_MT_sum, phi_MT1, phi_MT0


    
class dict_matrix():
    def __init__(self, val_type, nrows=False, ncols=False):

        self.val_type = val_type

        if val_type==float:
            self.rows = defaultdict(ddf)
            #self.cols = defaultdict(ddf)
        elif val_type==int:
            self.rows = defaultdict(ddi)
            #self.cols = defaultdict(ddi)
        elif val_type==bool:
            self.rows = defaultdict(ddb)
            #self.cols = defaultdict(ddb)
        else:
            print "UHOH"

    def set(self, row, col, val):
        if val == 0 and row in self.rows and col in self.rows[row]:
            del self.rows[row][col]
            #del self.cols[col][row]
        elif val != 0:
            self.rows[row][col] = val
            #self.cols[col][row] = val
    
    def get(self, row, col):
        return self.rows[row][col]

    def item_add(self, row, col, val):
        if val != 0:
            #print "hi?", self.rows[row][col], val
            self.rows[row][col] += val
            #self.cols[col][row] += val
            #print "hi!", self.rows[row][col], val
    
    def item_sub(self, row, col, val):
        if val != 0:
            self.rows[row][col] -= val
            #self.cols[col][row] -= val
    
    def item_div(self, row, col, val):
        if val != 0:
            self.rows[row][col] /= val

    def copy(self, mult=1.0):
        rv = dict_matrix(self.val_type)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, self.rows[row][col] * mult)
        return rv

    def __add__(self, other):#, val_type=self.val_type):
        #if self.nrows != other.nrows or self.ncols != other.ncols:
        #    raise ValueError("Not the same shape! [self %dx%d; other %dx%d" % \
        #        (self.nrows, self.ncols, other.nrows, other.ncols))

        rv = dict_matrix(self.val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, self.rows[row][col])
        for row in other.rows:
            for col in other.rows[row]:
                rv.item_add(row, col, other.rows[row][col])
        return rv
    
    def __sub__(self, other):#, val_type=self.val_type):
        #if self.nrows != other.nrows or self.ncols != other.ncols:
        #    raise ValueError("Not the same shape! [self %dx%d; other %dx%d" % \
        #        (self.nrows, self.ncols, other.nrows, other.ncols))

        rv = dict_matrix(self.val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, self.rows[row][col])
        for row in other.rows:
            for col in other.rows[row]:
                rv.item_sub(row, col, other.rows[row][col])
        return rv
    
    def __iadd__(self, other):
        #if self.nrows != other.nrows or self.ncols != other.ncols:
        #    raise ValueError("Not the same shape! [self %dx%d; other %dx%d" % \
        #        (self.nrows, self.ncols, other.nrows, other.ncols))

        for row in other.rows:
            for col in other.rows[row]:
                if other.rows[row][col] != 0:
                    self.item_add(row, col, other.rows[row][col])
        return self

    def row_add(self, row_id, other, indexes=None, mult=1):
        #for col_id in other.rows[row_id]:
        #    self.item_add(row_id, col_id, other.rows[row_id][col_id])
        if indexes:
            for c in xrange(len(indexes)):
                self.item_add(row_id, indexes[c], other[c] * mult)
        else:
            for col_id in other.cols:
                self.item_add(row_id, col_id, other.cols[col_id] * mult)
    
    def multiply(self, other):
        rv = dict_matrix(self.val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in set(self.rows[row]) & set(other.rows[row]):
                rv.set(row, col, self.rows[row][col] * other.rows[row][col])
        return rv

    #def __mult__(self, const):
    def const_multiply(self, const):
        rv = dict_matrix(self.val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, self.rows[row][col] * const)
        return rv

    def double_multiply(self, in_row, in_col):
        rv = dict_matrix(val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, self.rows[row][col] * \
                    in_row[col] * in_col[row]) #TODO:chaneg row to row_id and in_col to col
        return rv

    def multiply_row(self, self_row_id, other, other_row_id, const_div=1, printer=False):
        rv = dict_row(self.val_type)
        for col in self.rows[self_row_id]:
            if printer:
                print "multiply row:", col, self.rows[self_row_id][col], other.rows[other_row_id][col], const_div
            rv.set(col, self.rows[self_row_id][col] * \
                other.rows[other_row_id][col] / const_div)
        return rv
    
    def multiply_row_select(self, self_row_id, other, other_row_id, const_div=1):
        rv = dict_row(self.val_type)
        for col in self.rows[self_row_id]:
            if other_row_id in other[col]:
                rv.set(col, self.rows[self_row_id][col] / const_div)
        return rv
                
    def __div__(self, other):
        rv = dict_matrix(self.val_type)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in set(self.rows[row]) & set(other.rows[row]):
                rv.set(row, col, self.rows[row][col] / other.rows[row][col])
        return rv

    def sums(self):
        rvA = dict_row(self.val_type)#, self.ncols)
        rvB = dict_row(self.val_type)#, self.nrows)
        for row in self.rows:
            for col in self.rows[row]:
                rvA.item_add(col, self.rows[row][col])
                rvB.item_add(row, self.rows[row][col])
        return rvA, rvB
        
    def sum(self, axis=None):
        if axis == 0:
            rv = dict_row(self.val_type)
            for row in self.rows:
                for col in self.rows[row]:
                    rv.item_add(col, self.rows[row][col])
            return rv

        if axis == 1:
            rv = dict_row(self.val_type)#, self.nrows)
            for row in self.rows:
                for col in self.rows[row]:
                    rv.item_add(row, self.rows[row][col])
            return rv
        
        if not axis:
            rv = 0
            for row in self.rows:
                for col in self.rows[row]:
                    rv += self.rows[row][col]
            return rv

    def get_ave(self):
        rv = 0
        rc = 0
        for row in self.rows:
            for col in self.rows[row]:
                rv += self.rows[row][col]
                rc += 1
        return rv / rc
        
              
    def log(self):
        rv = dict_matrix(float)#, self.nrows, self.ncols)
        for row in self.rows:
            for col in self.rows[row]:
                rv.set(row, col, np.log(self.rows[row][col]))
        return rv
    
    def mean(self):
        rv = 0.
        counter = 0
        for row in self.rows:
            for col in self.rows[row]:
                rv += self.rows[row][col]
                counter += 1
        return rv / counter
   
    def log_gamma_sum(self):
        rv = 0
        for row in self.rows:
            for col in self.rows[row]:
                rv += gammaln(self.rows[row][col])
        return rv

    def set_row(self, row_id, row):
        for col in self.rows[row_id]:
            self.set(row_id, col, row[col])

def ddi():
    return defaultdict(int)
def ddf():
    return defaultdict(float)
def ddb():
    return defaultdict(bool)
class dataset:
    def __init__(self, model):
        self.binary = model.binary
        self.train_triplets = []

        self.training = triplets()
        self.validation = triplets()

        self.item_counts = defaultdict(int)
        if not model.binary:
            self.sparse_ratings = dict_matrix(int)#, model.item_count, model.user_count)
            self.sparse_vratings = dict_matrix(int)#, model.item_count, model.user_count)

        self.friends = defaultdict(list)
        self.friend_counts = defaultdict(ddi)
        self.friend_matrix = dict_matrix(bool)#, model.user_count, model.user_count)

        self.user_data = defaultdict(list)
        self.item_data = defaultdict(list)

    def has_rating(self, user, item):
        #binary only
        return (item in self.user_data[user] or item in self.user_datav[user])

    def shares(self, user, item):
        f = set()
        # for binary only 
        if self.binary:
            for friend in self.friends[user]:
                if item in self.user_data[friend]:
                    f.add(friend)
        else:
            for friend in self.friends[user]:
                if item in set([i for i,r in self.user_data[friend]]):
                    f.add(friend)
        return f
                
    def read_ratings(self, model, filename):
        i = 0
        user_count = len(model.users)
        item_count = len(model.items)
        self.training.size = 0
        '''counts = defaultdict(int)
        for line in open(filename, 'r'):
            triplet = tuple([int(x.strip()) for x in line.split('\t')])
            user, item, rating = triplet
            counts[item] += 1'''

        for line in open(filename, 'r'):
            if i % 500000 == 0:
                print i
            i += 1
            triplet = tuple([int(x.strip()) for x in line.split('\t')])
            user, item, rating = triplet

            if rating == 0:# or counts[item] < 2:
                continue
            if model.binary:
                self.train_triplets.append((user, item))
            else:
                self.train_triplets.append(triplet)

            if user not in model.users:
                model.users[user] = user_count#len(model.users)
                user_count += 1
            
            if item not in model.items:
                model.items[item] = item_count#len(model.items)
                item_count += 1

            self.item_counts[item] += 1
            if not model.binary:
                self.sparse_ratings.set(model.items[item], model.users[user], rating)

                self.user_data[model.users[user]].append((model.items[item], rating))
                self.item_data[model.items[item]].append((model.users[user], rating))
            else:
                self.user_data[model.users[user]].append(model.items[item])
                #self.item_data[model.items[item]].append(model.users[user])

        #model.user_count = len(model.users)
        model.user_count = user_count
        model.item_count = item_count
        #model.item_count = len(model.items)
        #print model.items[890]


    def read_validation(self, model, filename, user_set=False, item_set=False):
        users = []
        items = []
        ratings = []
        friends = []
        self.user_datav = defaultdict(list)
        for line in open(filename, 'r'):
            user, item, rating = \
                tuple([int(x.strip()) for x in line.split('\t')])
            if user not in model.users or item not in model.items:
                continue
            if (user_set and user not in user_set) or (item_set and item not in item_set):
                continue
            users.append(model.users[user])
            items.append(model.items[item])
            ratings.append(rating)
            friends.append(self.friend_counts[model.users[user]][model.items[item]])
            
            if not model.binary:
                self.user_datav[model.users[user]].append((model.items[item], rating))
                self.sparse_vratings.set(model.items[item], model.users[user], rating)
            else:
                self.user_datav[model.users[user]].append(model.items[item])

        self.validation.users = users
        self.validation.items = items
        self.validation.ratings = ratings
        self.validation.friend_counts = friends
        self.validation.size = len(users)


    def read_network(self, model, filename):
        print "reading network...."
        
        for line in open(filename, 'r'):
            if ',' in line:
                user, friend = tuple([int(x.strip()) for x in line.split(',')])
            else:
                user, friend = tuple([int(x.strip()) for x in line.split('\t')])
            if user not in model.users or friend not in model.users:
                continue
            f = "f"+str(friend)
            u = "f"+str(friend)
            duple = (user, f)
            duple2 = (friend, u)
            triplet = (user, f, 1)
            triplet2 = (friend, u, 1)
            user = model.users[user]
            friend = model.users[friend]

            if model.sorec:
                if f not in model.items:
                    model.items[f] = model.item_count
                    model.item_count += 1
                item = model.items[f]
                if not model.directed:
                    if u not in model.items:
                        model.items[u] = model.item_count
                        model.item_count += 1
                    item2 = model.items[u]

                if model.binary:
                    self.train_triplets.append(duple)
                    if not model.directed:
                        self.train_triplets.append(duple2)
                else:
                    self.train_triplets.append(triplet)
                    if not model.directed:
                        self.train_triplets.append(triplet2)

                self.item_counts[item] += 1
                if not model.directed:
                    self.item_counts[item2] += 1
                if not model.binary:
                    self.sparse_ratings.set(item, user, 1)

                    self.user_data[user].append((item, 1))
                    self.item_data[item].append((user, 1))
                    if not model.directed:
                        self.sparse_ratings.set(item2, friend, 1)

                        self.user_data[friend].append((item2, 1))
                        self.item_data[item2].append((friend, 1))
                else:
                    self.user_data[user].append(item)
                    if not model.directed:
                        self.user_data[friend].append(item2)

            if model.trust:
                if friend not in self.friends[user] and user != friend:
                    self.friends[user].append(friend)
                    self.friend_matrix.set(user, friend, True)
                    if model.undirected:
                        self.friends[friend].append(user)
                        self.friend_matrix.set(friend, user, True)
                    
                    if model.binary:
                        for item in self.user_data[friend]:
                            self.friend_counts[user][item] += 1
                    else:
                        for item, rating in self.user_data[friend]:
                            self.friend_counts[user][item] += 1
                    if model.undirected:
                        if model.binary:
                            for item in self.user_data[user]:
                                self.friend_counts[friend][item] += 1
                        else:
                            for item, rating in self.user_data[user]:
                                self.friend_counts[friend][item] += 1

        print "  network done"

    def items_in_common(self, user, friend):
        return len(set(self.user_data[user]) & set(self.user_data[friend]))
