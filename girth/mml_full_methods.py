import numpy as np
from scipy import integrate, stats
from scipy.optimize import fminbound, fmin_powell, fmin_slsqp

from girth import (irt_evaluation, convert_responses_to_kernel_sign,
                   validate_estimation_options, mml_approx)
from girth.utils import _get_quadrature_points, _compute_partial_integral
from girth.polytomous_utils import condition_polytomous_response, _credit_partial_integral


def rasch_full(dataset, discrimination=1, options=None):
    """ Estimates difficulty parameters in Rash IRT model.

    Args:
        dataset: [items x participants] matrix of True/False Values
        discrimination: scalar of discrimination used in model (default to 1)
        options: dictionary with updates to default options

    Returns:
        difficulty: (1d array) difficulty estimates

    Options:
        * max_iteration: int
        * distribution: callable
        * quadrature_bounds: (float, float)
        * quadrature_n: int
    """
    return onepl_full(dataset, alpha=discrimination, options=options)[1]


def onepl_full(dataset, alpha=None, options=None):
    """ Estimates parameters in an 1PL IRT Model.

    This function is slow, please use onepl_mml

    Args:
        dataset: [items x participants] matrix of True/False Values
        alpha: scalar of discrimination used in model (default to 1)
        options: dictionary with updates to default options

    Returns:
        discrimination: (float) estimate of test discrimination
        difficulty: (1d array) estimates of item diffiulties

    Options:
        * max_iteration: int
        * distribution: callable
        * quadrature_bounds: (float, float)
        * quadrature_n: int

    Notes:
        If alpha is supplied then this solves a Rasch model
    """
    options = validate_estimation_options(options)
    quad_start, quad_stop = options['quadrature_bounds']
    quad_n = options['quadrature_n']

    n_items = dataset.shape[0]
    unique_sets, counts = np.unique(dataset, axis=1, return_counts=True)
    the_sign = convert_responses_to_kernel_sign(unique_sets)

    theta = _get_quadrature_points(quad_n, quad_start, quad_stop)
    distribution = options['distribution'](theta)

    discrimination = np.ones((n_items,))
    difficulty = np.zeros((n_items,))

    def alpha_min_func(alpha_estimate):
        discrimination[:] = alpha_estimate

        for iteration in range(options['max_iteration']):
            previous_difficulty = difficulty.copy()

            # Quadrature evaluation for values that do not change
            partial_int = _compute_partial_integral(theta, difficulty,
                                                    discrimination, the_sign)
            partial_int *= distribution

            for item_ndx in range(n_items):
                # pylint: disable=cell-var-from-loop

                # remove contribution from current item
                local_int = _compute_partial_integral(theta, difficulty[item_ndx, None],
                                                      discrimination[item_ndx, None],
                                                      the_sign[item_ndx, None])

                partial_int /= local_int

                def min_local_func(beta_estimate):
                    difficulty[item_ndx] = beta_estimate

                    estimate_int = _compute_partial_integral(theta, difficulty[item_ndx, None],
                                                             discrimination[item_ndx, None],
                                                             the_sign[item_ndx, None])

                    estimate_int *= partial_int

                    otpt = integrate.fixed_quad(
                        lambda x: estimate_int, quad_start, quad_stop, n=quad_n)[0]

                    return -np.log(otpt).dot(counts)

                fminbound(min_local_func, -4, 4)

                # Update the partial integral based on the new found values
                estimate_int = _compute_partial_integral(theta, difficulty[item_ndx, None],
                                                         discrimination[item_ndx, None],
                                                         the_sign[item_ndx, None])
                # update partial integral
                partial_int *= estimate_int

            if(np.abs(previous_difficulty - difficulty).max() < 1e-3):
                break

        cost = integrate.fixed_quad(
            lambda x: partial_int, quad_start, quad_stop, n=quad_n)[0]
        return -np.log(cost).dot(counts)

    if alpha is None:  # OnePl Solver
        alpha = fminbound(alpha_min_func, 0.1, 4)
    else:  # Rasch Solver
        alpha_min_func(alpha)

    return alpha, difficulty


def twopl_full(dataset, options=None):
    """ Estimates parameters in a 2PL IRT model.

    Please use twopl_mml instead.

    Args:
        dataset: [items x participants] matrix of True/False Values
        options: dictionary with updates to default options

    Returns:
        discrimination: (1d array) estimates of item discrimination
        difficulty: (1d array) estimates of item difficulties

    Options:
        * max_iteration: int
        * distribution: callable
        * quadrature_bounds: (float, float)
        * quadrature_n: int
"""
    options = validate_estimation_options(options)
    quad_start, quad_stop = options['quadrature_bounds']
    quad_n = options['quadrature_n']

    n_items = dataset.shape[0]
    unique_sets, counts = np.unique(dataset, axis=1, return_counts=True)
    the_sign = convert_responses_to_kernel_sign(unique_sets)

    theta = _get_quadrature_points(quad_n, quad_start, quad_stop)
    distribution = options['distribution'](theta)

    discrimination = np.ones((n_items,))
    difficulty = np.zeros((n_items,))

    for iteration in range(options['max_iteration']):
        previous_discrimination = discrimination.copy()

        # Quadrature evaluation for values that do not change
        partial_int = _compute_partial_integral(theta, difficulty,
                                                discrimination, the_sign)
        partial_int *= distribution

        for item_ndx in range(n_items):
            # pylint: disable=cell-var-from-loop
            local_int = _compute_partial_integral(theta, difficulty[item_ndx, None],
                                                  discrimination[item_ndx, None],
                                                  the_sign[item_ndx, None])

            partial_int /= local_int

            def min_func_local(estimate):
                discrimination[item_ndx] = estimate[0]
                difficulty[item_ndx] = estimate[1]

                estimate_int = _compute_partial_integral(theta,
                                                         difficulty[item_ndx, None],
                                                         discrimination[item_ndx, None],
                                                         the_sign[item_ndx, None])

                estimate_int *= partial_int
                otpt = integrate.fixed_quad(
                    lambda x: estimate_int, quad_start, quad_stop, n=quad_n)[0]

                return -np.log(otpt).dot(counts)

            # Two parameter solver that doesn't need derivatives
            initial_guess = np.concatenate((discrimination[item_ndx, None],
                                            difficulty[item_ndx, None]))
            fmin_slsqp(min_func_local, initial_guess, disp=False,
                       bounds=[(0.25, 4), (-4, 4)])

            # Update the partial integral based on the new found values
            estimate_int = _compute_partial_integral(theta, difficulty[item_ndx, None],
                                                     discrimination[item_ndx, None],
                                                     the_sign[item_ndx, None])
            # update partial integral
            partial_int *= estimate_int

        if(np.abs(discrimination - previous_discrimination).max() < 1e-3):
            break

    return discrimination, difficulty


def pcm_mml(dataset, options=None):
    """Estimate parameters for partial credit model.

    Estimate the discrimination and difficulty parameters for
    the partial credit model using marginal maximum likelihood.

    Args:
        dataset: [n_items, n_participants] 2d array of measured responses
        options: dictionary with updates to default options

    Returns:
        discrimination: (1d array) estimates of item discrimination
        difficulty: (2d array) estimates of item difficulties x item thresholds

    Options:
        * max_iteration: int
        * distribution: callable
        * quadrature_bounds: (float, float)
        * quadrature_n: int
    """
    options = validate_estimation_options(options)
    quad_start, quad_stop = options['quadrature_bounds']
    quad_n = options['quadrature_n']

    responses, item_counts = condition_polytomous_response(dataset, trim_ends=False,
                                                           _reference=0.0)
    n_items = responses.shape[0]

    # Interpolation Locations
    theta = _get_quadrature_points(quad_n, quad_start, quad_stop)
    distribution = options['distribution'](theta)

    # Initialize difficulty parameters for estimation
    betas = np.full((n_items, item_counts.max()), np.nan)
    discrimination = np.ones((n_items,))
    partial_int = np.ones((responses.shape[1], theta.size))

    # Not all items need to have the same
    # number of response categories
    betas[:, 0] = 0
    for ndx in range(n_items):
        betas[ndx, 1:item_counts[ndx]] = np.linspace(-1, 1, item_counts[ndx]-1)

    #############
    # 1. Start the iteration loop
    # 2. Estimate Dicriminatin/Difficulty Jointly
    # 3. Integrate of theta
    # 4. minimize and repeat
    #############
    for iteration in range(options['max_iteration']):
        previous_discrimination = discrimination.copy()
        previous_betas = betas.copy()

        # Quadrature evaluation for values that do not change
        # This is done during the outer loop to address rounding errors
        # and for speed
        partial_int *= 0.0
        partial_int += distribution[None, :]
        for item_ndx in range(n_items):
            partial_int *= _credit_partial_integral(theta, betas[item_ndx],
                                                    discrimination[item_ndx],
                                                    responses[item_ndx])

        # Loop over each item and solve for the alpha / beta parameters
        for item_ndx in range(n_items):
            # pylint: disable=cell-var-from-loop
            item_length = item_counts[item_ndx]
            new_betas = np.zeros((item_length))

            # Remove the previous output
            old_values = _credit_partial_integral(theta, previous_betas[item_ndx],
                                                  previous_discrimination[item_ndx],
                                                  responses[item_ndx])
            partial_int /= old_values

            def _local_min_func(estimate):
                new_betas[1:] = estimate[1:]
                new_values = _credit_partial_integral(theta, new_betas,
                                                      estimate[0],
                                                      responses[item_ndx])

                new_values *= partial_int
                otpt = integrate.fixed_quad(
                    lambda x: new_values, quad_start, quad_stop, n=quad_n)[0]

                return -np.log(otpt).sum()

            # Univariate minimization for discrimination parameter
            initial_guess = np.concatenate(([discrimination[item_ndx]],
                                            betas[item_ndx, 1:item_length]))

            otpt = fmin_slsqp(_local_min_func, initial_guess,
                              disp=False,
                              bounds=[(.25, 4)] + [(-6, 6)] * (item_length - 1))

            discrimination[item_ndx] = otpt[0]
            betas[item_ndx, 1:item_length] = otpt[1:]

            new_values = _credit_partial_integral(theta, betas[item_ndx],
                                                  discrimination[item_ndx],
                                                  responses[item_ndx])

            partial_int *= new_values

        if np.abs(previous_discrimination - discrimination).max() < 1e-3:
            break

    # TODO:  look where missing values are and place NAN there instead
    # of appending them to the end
    return discrimination, betas[:, 1:]
