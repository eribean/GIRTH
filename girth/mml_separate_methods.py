import numpy as np
from scipy import integrate
from scipy.optimize import fminbound, brentq

from girth import irt_evaluation, rasch_approx, condition_polytomous_response
from girth.utils import _get_quadrature_points, _compute_partial_integral
from girth.polytomous_utils import (_graded_partial_integral, _solve_for_constants, 
                                    _solve_integral_equations)


def rasch_separate(dataset, discrimination=1, max_iter=25):
    """
        Estimates parameters in an IRT model with full
        gaussian quadrature

        Args:
            dataset: [items x participants] matrix of True/False Values
            discrimination: scalar of discrimination used in model (default to 1)
            max_iter: maximum number of iterations to run

        Returns:
            array of discrimination estimates
    """
    n_items = dataset.shape[0]
    n_no = np.count_nonzero(~dataset, axis=1)
    n_yes = np.count_nonzero(dataset, axis=1)
    scalar = n_yes / (n_yes + n_no)

    if np.ndim(discrimination) < 1:
        discrimination = np.full(n_items, discrimination)

    # Inline definition of quadrature function
    def quadrature_function(theta, difficulty, discrimination):
        gauss = 1.0 / np.sqrt(2 * np.pi) * np.exp(-np.square(theta) / 2)
        return irt_evaluation(np.array([difficulty]),
                              np.array([discrimination]), theta) * gauss

    # Initialize the discrimination parameters
    the_parameters = np.zeros((n_items,))

    # Perform the minimization
    for ndx in range(n_items):

        # Minimize each item separately
        def min_zero_local(estimate):
            return (scalar[ndx] -
                    integrate.fixed_quad(quadrature_function, -10, 10,
                    (estimate, discrimination[ndx]), n=101)[0])

        the_parameters[ndx] = brentq(min_zero_local, -6, 6)

    return the_parameters


def onepl_separate(dataset):
    """
        Estimates the difficulty and single discrimination parameter

        Separates the difficulty estimation from the discrimination
        parameters

        Args:
            dataset: [items x participants] matrix of True/False Values

        Returns:
            array of discrimination, difficulty estimates
    """
    unique_sets, counts = np.unique(dataset, axis=1, return_counts=True)
    the_sign = (-1)**unique_sets
    
    # Inline definition of cost function to minimize
    def min_func(estimate):
        difficulty = rasch_separate(dataset, estimate)
        otpt = integrate.fixed_quad(_compute_partial_integral, -5, 5,
                                    (difficulty, estimate, the_sign), n=61)[0]

        return -np.log(otpt).dot(counts)

    # Perform the minimization
    discrimination = fminbound(min_func, 0.25, 10)

    return discrimination, rasch_separate(dataset, discrimination)


def twopl_separate(dataset, max_iter=25):
    """
        Estimates the difficulty and discrimination parameters

        Separates the difficulty estimation from the discrimination
        parameters

        Args:
            dataset: [items x participants] matrix of True/False Values
            max_iter:  maximum number of iterations to run

        Returns:
            array of discrimination, difficulty estimates
    """
    n_items = dataset.shape[0]
    unique_sets, counts = np.unique(dataset, axis=1, return_counts=True)
    the_sign = (-1)**unique_sets

    theta = _get_quadrature_points(61, -5, 5)

    # Inline definition of quadrature function
    def quadrature_function(theta, discrimination, old_discrimination,
                            difficulty, old_difficulty,
                            partial_int, the_sign):
        kernel1 = the_sign[:, None] * (theta[None, :] - difficulty)
        kernel1 *= discrimination

        kernel2 = the_sign[:, None] * (theta[None, :] - old_difficulty)
        kernel2 *= old_discrimination

        return partial_int * (1 + np.exp(kernel2)) / (1 + np.exp(kernel1))


    # Inline definition of cost function to minimize
    def min_func(estimate, dataset, old_estimate, old_difficulty,
                 partial_int, the_sign):
        new_difficulty = rasch_separate(dataset, estimate)
        otpt = integrate.fixed_quad(quadrature_function, -5, 5,
                                    (estimate, old_estimate,
                                     new_difficulty, old_difficulty,
                                     partial_int, the_sign), n=61)[0]
        return -np.log(otpt).dot(counts)

    # Perform the minimization
    initial_guess = np.ones((dataset.shape[0],))
    difficulties = rasch_approx(dataset)

    for iteration in range(max_iter):
        previous_guess = initial_guess.copy()
        previous_difficulty = difficulties.copy()

        # Quadrature evaluation for values that do not change
        # This is done during the outer loop to address rounding errors
        partial_int = _compute_partial_integral(theta, difficulties,
                          initial_guess, the_sign)

        for ndx in range(n_items):
            def min_func_local(estimate):
                return min_func(estimate, dataset[ndx].reshape(1, -1),
                                previous_guess[ndx],
                                previous_difficulty[ndx],
                                partial_int, the_sign[ndx])

            # Solve for the discrimination parameters
            initial_guess[ndx] = fminbound(min_func_local, 0.25, 6, xtol=1e-3)
            difficulties[ndx] = rasch_separate(dataset[ndx].reshape(1, -1),
                                                   initial_guess[ndx])

            # Update the partial integral based on the new found values
            partial_int = quadrature_function(theta, initial_guess[ndx],
                                              previous_guess[ndx], difficulties[ndx],
                                              previous_difficulty[ndx],
                                              partial_int, the_sign[ndx])

        if np.abs(initial_guess - previous_guess).max() < 1e-3:
            break

    return initial_guess, difficulties


def grm_separate(dataset, max_iter=25):
    """Estimate parameters for graded response model.

    Estimate the discrimination and difficulty parameters for
    a graded response model using marginal maximum likelihood.

    Args:
        dataset: [n_items, n_participants] 2d array of measured responses
        max_iter: (optional) maximum number of iterations to perform

    Returns:
        array of discrimination parameters
        2d array of difficulty parameters, (NAN represents non response)
    """
    responses, item_counts = condition_polytomous_response(dataset, trim_ends=False)    
    n_items = responses.shape[0]
    
    # Interpolation Locations
    theta = _get_quadrature_points(61, -5, 5)
    distribution = np.exp(-np.square(theta) / 2) / np.sqrt(2 * np.pi)
    ones_distribution = np.ones_like(distribution)
    
    # Compute the values needed for integral equations
    integral_counts = list()
    for ndx in range(n_items):
        temp_output = _solve_for_constants(responses[ndx])
        integral_counts.append(temp_output)
                    
    # Initialize difficulty parameters for estimation
    betas = np.full((item_counts.sum(),), -10000.0)
    discrimination = np.ones_like(betas)
    cumulative_item_counts = item_counts.cumsum()
    start_indices = np.roll(cumulative_item_counts, 1)
    start_indices[0] = 0

    for ndx in range(n_items):
        end_ndx = cumulative_item_counts[ndx]
        start_ndx = start_indices[ndx] + 1
        betas[start_ndx:end_ndx] = np.linspace(-1, 1, 
                                               item_counts[ndx] - 1)
    betas_roll = np.roll(betas, -1)
    betas_roll[cumulative_item_counts-1] = 10000

    #############
    ## 1. Start the iteration loop
    ## 2. estimate discrimination
    ## 3. solve for difficulties
    ## 4. minimize and repeat
    #############
    for iteration in range(max_iter):
        previous_discrimination = discrimination.copy()
        previous_betas = betas.copy()
        previous_betas_roll = betas_roll.copy()
        
        # Quadrature evaluation for values that do not change
        # This is done during the outer loop to address rounding errors
        partial_int = _graded_partial_integral(theta, betas, betas_roll,
                                               discrimination, responses,
                                               distribution)
        
        for item_ndx in range(n_items):
            # Indices into linearized difficulty parameters
            start_ndx = start_indices[item_ndx]
            end_ndx = cumulative_item_counts[item_ndx]
            
            old_values = _graded_partial_integral(theta, previous_betas,
                                                  previous_betas_roll, 
                                                  previous_discrimination,
                                                  responses[item_ndx][None, :],
                                                  ones_distribution)
            partial_int /= old_values
            
            def _local_min_func(estimate):
                # Solve integrals for diffiulty estimates
                new_betas = _solve_integral_equations(estimate, 
                                                      integral_counts[item_ndx],
                                                      distribution,
                                                      theta)
                betas[start_ndx+1:end_ndx] = new_betas
                betas_roll[start_ndx:end_ndx-1] = new_betas
                discrimination[start_ndx:end_ndx] = estimate
                
                new_values = _graded_partial_integral(theta, betas, betas_roll,
                                                      discrimination,
                                                      responses[item_ndx][None, :],
                                                      ones_distribution)
                
                new_values *= partial_int
                otpt = integrate.fixed_quad(lambda x: new_values, -5, 5, n=61)[0]
                
                return -np.log(otpt).sum()
            
            # Univariate minimization for discrimination parameter
            fminbound(_local_min_func, 0.2, 5.0)
            
            new_values = _graded_partial_integral(theta, betas, betas_roll,
                                                  discrimination,
                                                  responses[item_ndx][None, :],
                                                  ones_distribution)

            partial_int *= new_values
            
        
        if np.abs(previous_discrimination - discrimination).max() < 1e-3:
            break
    
    # Trim difficulties to conform to standard output
    #TODO:  look where missing values are and place NAN there instead
    # of appending them to the end
    output_betas = np.full((n_items, item_counts.max()-1), np.nan)
    for ndx, (start_ndx, end_ndx) in enumerate(zip(start_indices, cumulative_item_counts)):
        output_betas[ndx, :end_ndx-start_ndx-1] = betas[start_ndx+1:end_ndx]
        
    return discrimination[start_indices], output_betas
